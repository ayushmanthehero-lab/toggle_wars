"""Atomic state transitions and immutable attempt/decision evidence."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
import time
import uuid

from fastapi import HTTPException

from .db import Database, dump, now


TERMINAL = {'passed', 'failed', 'blocked', 'error', 'cancelled'}


def digest(value):
    return hashlib.sha256(dump(value).encode()).hexdigest()


def safe_text(value):
    if not isinstance(value, str):
        return value
    value = value[:32000]
    for name in ('CI_GUARD_TOKEN', 'CI_GUARD_ADMIN_TOKEN', 'FLAGSMITH_ENVIRONMENT_KEY', 'FLAGSMITH_ADMIN_API_KEY', 'CI_GUARD_GITHUB_TOKEN'):
        secret = os.getenv(name)
        if secret:
            value = value.replace(secret, '[REDACTED]')
    value = re.sub(r'cg_proj_[A-Za-z0-9_-]+','[REDACTED]',value)
    value = re.sub(r'(?i)(authorization\s*[:=]\s*(?:bearer\s+)?|(?:api[_-]?key|token|password|secret)\s*[:=]\s*)[^\s,;]+', r'\1[REDACTED]', value)
    return re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', value)


def sanitize(value):
    if isinstance(value, dict):
        return {key: ('[REDACTED]' if re.search(r'(?i)(token|password|secret|api.?key|authorization)', key) else sanitize(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return safe_text(value)


def timestamp_after(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def due(timestamp):
    return timestamp is None or datetime.fromisoformat(timestamp) <= datetime.now(timezone.utc)


class Coordinator:
    def __init__(self, db, provider, demo_mode=False):
        self.db, self.provider, self.demo_mode = db, provider, demo_mode

    @staticmethod
    def require(con, table, identifier):
        value = Database.get(con, table, identifier)
        if value is None:
            raise HTTPException(404, f'{table[:-1].capitalize()} not found')
        return value

    def register(self, values, update=False):
        with self.db.transaction() as con:
            existing = self.db.get(con, 'dependencies', values['id'])
            if existing and not update:
                raise HTTPException(409, 'Dependency already exists')
            if not existing and update:
                raise HTTPException(404, 'Dependency not found')
            if existing and any(existing[key] != values[key] for key in ('health_url','error_patterns')) and existing['_window_open']:
                raise HTTPException(409, 'Recover the active outage before changing its health URL or evidence signatures')
            if existing and any(existing[key] != values[key] for key in ('health_url','error_patterns')):
                existing.update(status='unknown',last_checked_at=None,health_detail=None,
                    _lease=None,_generation=existing['_generation']+1)
            dep = existing or {
                'status':'unknown', 'last_checked_at':None, 'health_detail':None,
                'next_probe_at':None, 'opened_at':None, '_window_open':False, '_generation':0,
                '_enforce_used':0, '_observe_used':0, '_enforce_cap':None, '_observe_cap':None,
                '_lease':None,
            }
            dep.update(values)
            if existing:
                self.db.save(con, 'dependencies', dep)
            else:
                con.execute('INSERT INTO dependencies VALUES (?,?)', (dep['id'],dump(dep)))
            self.db.audit(con, 'dependency_updated' if update else 'dependency_created', dep['name'], dependency=dep['id'])
            return self.public_dependency(con, dep)

    @staticmethod
    def public_dependency(con, dep, policy=None):
        result = {k:v for k,v in dep.items() if not k.startswith('_')}
        mode = 'observe' if policy and policy.get('mode') == 'observe' else 'enforce'
        result['retries_used'] = dep[f'_{mode}_used']
        result['shadow_retries_used'] = dep['_observe_used']
        result['budget'] = dep[f'_{mode}_cap'] if dep[f'_{mode}_cap'] is not None else (policy or {}).get('shared_dependency_retry_budget', 2)
        result['window_open'] = dep['_window_open']
        result['probe_in_flight'] = bool(dep['_lease'] and not due(dep['_lease']['expires_at']))
        result['active_runs'] = con.execute("SELECT count(*) FROM runs WHERE json_extract(data,'$.dependency')=? AND json_extract(data,'$.status') IN ('queued','running')", (dep['id'],)).fetchone()[0]
        return result

    def dependencies(self, repository=''):
        policy = self.provider.evaluate(repository).model_dump()
        with self.db.transaction() as con:
            return [self.public_dependency(con,json.loads(row['data']),policy) for row in con.execute('SELECT data FROM dependencies ORDER BY id')]

    @staticmethod
    def constrain(dep, policy):
        if policy['mode'] not in ('observe','enforce') or policy.get('error'):
            return
        key = f'_{policy["mode"]}_cap'
        requested = policy['shared_dependency_retry_budget']
        dep[key] = requested if dep[key] is None else min(dep[key], requested)

    @staticmethod
    def decision(action, reason, dep, policy, **extra):
        mode = 'observe' if policy['mode'] == 'observe' else 'enforce'
        cap = dep[f'_{mode}_cap']
        cap = policy['shared_dependency_retry_budget'] if cap is None else cap
        return {'action':action, 'reason':reason, 'retry_after_seconds':0,
            'shared_remaining':max(0,cap-dep[f'_{mode}_used']), 'would_block':False,
            'policy':policy, 'created_at':now(), **extra}

    def save_decision(self, con, run, decision):
        run['decision'] = decision
        run['reason'] = decision['reason']
        if decision['action'] == 'block':
            run['status'] = 'blocked'
            run['finished_at'] = now()
            run['prevented_attempts'] += 1
        elif decision.get('would_block'):
            run['would_prevent_attempts'] += 1
        con.execute('INSERT INTO decisions(run_id,data) VALUES (?,?)',(run['id'],dump(decision)))
        self.db.save(con,'runs',run)

    @staticmethod
    def lease(dep, owner):
        lease = {'owner':owner, 'token':str(uuid.uuid4()), 'generation':dep['_generation'],
            'expires_at':timestamp_after(dep['timeout_seconds']+5), 'started':False}
        dep['_lease'] = lease
        return lease

    def create_run(self, values):
        policy = self.provider.evaluate(values['repository']).model_dump()
        request_hash = digest(values)
        key = values.get('idempotency_key')
        if key and values.get('project_id'):
            key = f'project:{values["project_id"]}:{key}'
        with self.db.transaction() as con:
            if key:
                previous = con.execute('SELECT data,request_hash FROM runs WHERE idempotency_key=?',(key,)).fetchone()
                if previous:
                    if previous['request_hash'] != request_hash:
                        raise HTTPException(409,'Idempotency key already used with different run inputs')
                    return json.loads(previous['data'])
            dep = self.require(con,'dependencies',values['dependency'])
            run = {**sanitize(values), 'id':str(uuid.uuid4()), 'status':'running', 'created_at':now(),
                'finished_at':None, 'attempt_count':0, 'prevented_attempts':0,
                'would_prevent_attempts':0, 'duration_seconds':0.0, 'reason':None,
                'policy':policy, 'decision':None, 'demo':self.demo_mode}
            # Command is display-only metadata; the server never executes it.
            con.execute('INSERT INTO runs VALUES (?,?,?,?)',(run['id'],key,request_hash,dump(run)))
            if dep['_window_open']:
                self.constrain(dep,policy)
            recovery_probe_eligible = due(dep['next_probe_at']) and (
                dep['_lease'] is None or due(dep['_lease']['expires_at']))
            if policy.get('error'):
                decision = self.decision('run','Policy unavailable: execute once without retries',dep,policy)
            elif policy['mode'] == 'enforce' and dep['_window_open']:
                if recovery_probe_eligible:
                    lease = self.lease(dep,run['id'])
                    decision = self.decision('probe','Recovery probe is eligible; verify dependency health',dep,policy,
                        probe_token=lease['token'],probe_generation=lease['generation'])
                else:
                    reason = 'Another job owns the recovery probe' if dep['_lease'] and not due(dep['_lease']['expires_at']) else 'Confirmed dependency outage; cooldown has not elapsed'
                    decision = self.decision('block',reason,dep,policy)
            else:
                decision = self.decision('run','Execute the test command',dep,policy,
                    would_block=policy['mode']=='observe' and dep['_window_open'] and not recovery_probe_eligible)
            self.save_decision(con,run,decision)
            self.db.save(con,'dependencies',dep)
            self.db.audit(con,'run_created',decision['reason'],run_id=run['id'],dependency=dep['id'])
            return run

    def record_attempt(self, run_id, values):
        # Evaluate outside the write transaction; each decision records one complete response.
        with self.db.transaction() as con:
            repository = self.require(con,'runs',run_id)['repository']
        policy = self.provider.evaluate(repository).model_dump()
        request_hash = digest(values)
        with self.db.transaction() as con:
            old = con.execute('SELECT request_hash,response FROM attempts WHERE run_id=? AND idempotency_key=?',
                (run_id,values['idempotency_key'])).fetchone()
            if old:
                if old['request_hash'] != request_hash:
                    raise HTTPException(409,'Idempotency key already used with different attempt evidence')
                return json.loads(old['response'])
            run = self.require(con,'runs',run_id)
            if run['status'] in TERMINAL:
                raise HTTPException(409,'Terminal runs cannot accept new attempts')
            if values['attempt_number'] != run['attempt_count']+1:
                raise HTTPException(409,'Attempt number must be the next sequential number; duplicate numbers are forbidden')
            if run['decision']['action'] not in ('run','retry'):
                raise HTTPException(409,'No command execution was authorized by the last decision')
            admitted_policy = run['decision']['policy']
            one_attempt_only = any(snapshot.get('error') or snapshot['mode']=='off'
                for snapshot in (policy,run['policy'],admitted_policy))
            dep = self.require(con,'dependencies',run['dependency'])
            run['attempt_count'] += 1
            run['duration_seconds'] += values['duration_seconds']
            evidence_text = values['evidence'] if isinstance(values.get('evidence'),str) else dump(values.get('evidence'))
            confirmed = (values['classification']=='dependency' and values['health_status']=='unhealthy'
                and dep['status']=='unhealthy' and (dep['_window_open'] or (dep['last_checked_at'] is not None
                    and (datetime.now(timezone.utc)-datetime.fromisoformat(dep['last_checked_at'])).total_seconds()<=30))
                and any(pattern in evidence_text for pattern in dep['error_patterns']))
            if values['classification']=='passed':
                run['status'],run['finished_at'] = 'passed',now()
                decision = self.decision('stop','Test command passed',dep,policy)
            elif one_attempt_only:
                run['status'],run['finished_at'] = 'failed',now()
                policy_unavailable = any(snapshot.get('error') for snapshot in (policy,run['policy'],admitted_policy))
                decision = self.decision('stop','Policy unavailable when the command was admitted or recorded; no retries allowed'
                    if policy_unavailable else 'One attempt completed; retries disabled by policy',dep,policy)
            elif not confirmed:
                run['status'],run['finished_at'] = 'failed',now()
                decision = self.decision('stop','Failure is not supported by a registered signature and confirmed unhealthy dependency',dep,policy)
            else:
                if not dep['_window_open']:
                    dep['_window_open'],dep['opened_at'] = True,now()
                    dep['_generation'] += 1
                    dep['next_probe_at'] = timestamp_after(policy['dependency_cooldown_seconds'])
                    # Any probe started before this evidence belongs to an older generation.
                    dep['_lease'] = None
                self.constrain(dep,policy)
                mode = policy['mode']
                cap = dep[f'_{mode}_cap']
                used = dep[f'_{mode}_used']
                per_job_allowed = run['attempt_count'] <= policy['max_job_retries']
                shared_allowed = used < cap
                if not per_job_allowed:
                    run['status'],run['finished_at'] = 'failed',now()
                    decision = self.decision('stop','Per-job retry allowance exhausted',dep,policy)
                elif mode=='observe':
                    dep['_observe_used'] += 1
                    decision = self.decision('retry','Observe mode permits the configured per-job retry',dep,policy,would_block=not shared_allowed)
                elif mode=='enforce' and shared_allowed:
                    dep['_enforce_used'] += 1
                    decision = self.decision('retry','Reserved one retry from the shared dependency allowance',dep,policy)
                elif mode=='enforce':
                    decision = self.decision('block','Shared retry allowance exhausted' if not shared_allowed else 'Per-job retry allowance exhausted',dep,policy)
                else:
                    run['status'],run['finished_at'] = 'failed',now()
                    decision = self.decision('stop','Per-job retry allowance exhausted',dep,policy)
            self.save_decision(con,run,decision)
            self.db.save(con,'dependencies',dep)
            attempt = {**sanitize(values),'created_at':now()}
            response = {'run':run,'decision':decision}
            con.execute('INSERT INTO attempts(run_id,attempt_number,idempotency_key,request_hash,data,response) VALUES (?,?,?,?,?,?)',
                (run_id,values['attempt_number'],values['idempotency_key'],request_hash,dump(attempt),dump(response)))
            self.db.audit(con,'attempt_recorded',decision['reason'],run_id=run_id,attempt_number=values['attempt_number'])
            return response

    def get_run(self, run_id):
        with self.db.transaction() as con:
            run = self.require(con,'runs',run_id)
            run['attempts'] = [json.loads(row['data']) for row in con.execute('SELECT data FROM attempts WHERE run_id=? ORDER BY attempt_number',(run_id,))]
            run['decisions'] = [json.loads(row['data']) for row in con.execute('SELECT data FROM decisions WHERE run_id=? ORDER BY id',(run_id,))]
            return run

    def list_runs(self, status=None, repository=None, search=None, limit=50, project_id=None, dependency_ids=None):
        clauses,params = [],[]
        for field,value in (('status',status),('repository',repository),('project_id',project_id)):
            if value:
                clauses.append(f"json_extract(data,'$.{field}')=?")
                params.append(value)
        if dependency_ids is not None:
            if not dependency_ids:
                return []
            clauses.append("json_extract(data,'$.dependency') IN ("+','.join('?' for _ in dependency_ids)+')')
            params.extend(dependency_ids)
        if search:
            clauses.append("(json_extract(data,'$.job_id') LIKE ? ESCAPE '\\' OR json_extract(data,'$.repository') LIKE ? ESCAPE '\\' OR id LIKE ? ESCAPE '\\')")
            escaped = search.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
            params.extend(['%'+escaped+'%']*3)
        sql = 'SELECT data FROM runs'+(' WHERE '+' AND '.join(clauses) if clauses else '')+" ORDER BY json_extract(data,'$.created_at') DESC LIMIT ?"
        with self.db.transaction() as con:
            return [json.loads(row['data']) for row in con.execute(sql,params+[limit])]

    def finish(self, run_id, values):
        with self.db.transaction() as con:
            run = self.require(con,'runs',run_id)
            if run['status'] in TERMINAL:
                if run['status'] != values['status']:
                    raise HTTPException(409,'Terminal run status cannot be changed')
                return run
            if values['status']=='passed':
                raise HTTPException(409,'Passed status requires a recorded successful attempt')
            run.update(status=values['status'],finished_at=now(),reason=safe_text(values.get('reason')) or 'Run finished by client')
            self.db.save(con,'runs',run)
            self.db.audit(con,'run_finished',run['reason'],run_id=run_id,status=run['status'])
            return run

    def probe(self, dependency_id, values, health_probe):
        owner = values.get('run_id')
        policy = None
        if owner:
            with self.db.transaction() as con:
                run = self.require(con,'runs',owner)
                if run['dependency'] != dependency_id:
                    raise HTTPException(409,'Run belongs to another dependency')
                if run['status'] in TERMINAL:
                    raise HTTPException(409,'Terminal run cannot acquire a health probe')
            policy = self.provider.evaluate(run['repository']).model_dump()
        else:
            policy = self.provider.evaluate('').model_dump()
        waiting = None
        with self.db.transaction() as con:
            dep = self.require(con,'dependencies',dependency_id)
            lease = dep['_lease']
            if values.get('probe_token') is not None:
                if not lease or lease['token']!=values['probe_token'] or lease['generation']!=values.get('probe_generation') or lease['owner']!=owner or due(lease['expires_at']) or lease['started']:
                    raise HTTPException(409,'Recovery probe lease is stale, already used, or owned by another run')
            elif (dep['_window_open'] and not due(dep['next_probe_at'])) or (lease and not due(lease['expires_at'])):
                if owner and lease and not due(lease['expires_at']) and lease['started']:
                    waiting = lease
                else:
                    result = self.public_dependency(con,dep,policy)
                    return {**result,'dependency':result,'detail':dep['health_detail'] or 'Recovery probe is not eligible yet','performed':False}
            else:
                lease = self.lease(dep,owner or 'admin:'+str(uuid.uuid4()))
            if waiting is None:
                lease['started'] = True
                self.db.save(con,'dependencies',dep)
        if waiting is not None:
            # Share an already-running HTTP check, never wait for the outage cooldown.
            # This prevents simultaneous first failures from losing verified evidence.
            deadline = time.monotonic()+dep['timeout_seconds']+0.5
            while True:
                with self.db.transaction() as con:
                    current = self.require(con,'dependencies',dependency_id)
                    active = current['_lease']
                    result = self.public_dependency(con,current,policy)
                if not active or active['token']!=waiting['token'] or time.monotonic()>=deadline:
                    return {**result,'dependency':result,'detail':current['health_detail'] or 'A bounded health probe is still in progress','performed':False}
                time.sleep(0.01)
        try:
            status,detail = health_probe(dep)
            if status not in ('healthy','unhealthy','unknown'):
                status,detail = 'unknown','Probe returned an invalid health status'
        except Exception:
            status,detail = 'unknown','Health probe could not be completed'
        with self.db.transaction() as con:
            current = self.require(con,'dependencies',dependency_id)
            active = current['_lease']
            if not active or active['token'] != lease['token'] or current['_generation'] != lease['generation'] or due(active['expires_at']):
                if not values.get('probe_token'):
                    result = self.public_dependency(con,current,policy)
                    return {**result,'dependency':result,'detail':current['health_detail'] or 'Health evidence changed during this check','performed':False}
                raise HTTPException(409,'Probe result arrived after its lease expired or outage generation changed')
            current.update(status=status,health_detail=safe_text(detail),last_checked_at=now())
            current['_lease'] = None
            if status=='healthy' and current['_window_open']:
                current.update(_window_open=False,opened_at=None,next_probe_at=None,
                    _enforce_used=0,_observe_used=0,_enforce_cap=None,_observe_cap=None)
            elif current['_window_open']:
                current['next_probe_at'] = timestamp_after(policy['dependency_cooldown_seconds'])
            decision = None
            if owner:
                run = self.require(con,'runs',owner)
                if run['status'] not in TERMINAL and run['decision']['action']=='probe':
                    if status=='healthy' or policy['mode']!='enforce' or policy.get('error'):
                        decision = self.decision('run','Dependency recovered after an eligible health probe' if status=='healthy' else 'Policy permits one command execution',current,policy,
                            would_block=policy['mode']=='observe' and status!='healthy')
                    else:
                        decision = self.decision('block','Recovery probe did not confirm a healthy dependency',current,policy)
                    self.save_decision(con,run,decision)
            self.db.save(con,'dependencies',current)
            self.db.audit(con,'dependency_probed',safe_text(detail),dependency=dependency_id,status=status,generation=lease['generation'])
            result = self.public_dependency(con,current,policy)
            return {**result,'dependency':result,'detail':safe_text(detail),'performed':True,**({'decision':decision} if decision else {})}

    def audit(self, limit=100):
        with self.db.transaction() as con:
            return [{'id':row['id'],**json.loads(row['data'])} for row in con.execute('SELECT id,data FROM audit ORDER BY id DESC LIMIT ?',(limit,))]

    def overview(self):
        policy = self.provider.evaluate('').model_dump()
        with self.db.transaction() as con:
            row = con.execute("""SELECT count(*) total_runs,
                coalesce(sum(json_extract(data,'$.status')='passed'),0) passed_runs,
                coalesce(sum(json_extract(data,'$.status') IN ('failed','error','cancelled')),0) failed_runs,
                coalesce(sum(json_extract(data,'$.status')='blocked'),0) blocked_runs,
                coalesce(sum(json_extract(data,'$.prevented_attempts')),0) prevented_attempts,
                coalesce(sum(json_extract(data,'$.would_prevent_attempts')),0) would_prevent_attempts,
                coalesce(sum(json_extract(data,'$.duration_seconds')),0) execution_seconds FROM runs""").fetchone()
            trend = [dict(item) for item in con.execute("""SELECT substr(json_extract(data,'$.created_at'),1,10) date,
                sum(json_extract(data,'$.attempt_count')) attempts, sum(json_extract(data,'$.prevented_attempts')) prevented
                FROM runs GROUP BY date ORDER BY date DESC LIMIT 30""")][::-1]
        return {'stats':dict(row),'trend':trend,'recent_runs':self.list_runs(limit=8),
            'dependencies':self.dependencies(),'policy':policy,'demo_mode':self.demo_mode,'policy_source':policy['source']}
