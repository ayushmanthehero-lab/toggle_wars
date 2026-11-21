from concurrent.futures import ThreadPoolExecutor
import importlib
import sqlite3

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('CI_GUARD_DEMO_MODE', 'true')
    monkeypatch.delenv('FLAGSMITH_ENVIRONMENT_KEY', raising=False)
    module = importlib.import_module('ci_guard.app')
    app = module.create_app(database_path=str(tmp_path / 'guard.db'))
    app.state.health_probe = lambda dep: ('unhealthy', 'HTTP 503')
    with TestClient(app) as client:
        assert client.post('/api/dependencies', json={
            'id': 'inventory', 'name': 'Inventory', 'health_url': 'http://inventory:9000/health',
            'error_patterns': ['INVENTORY_UNAVAILABLE'],
        }).status_code == 201
        yield client


def policy(client, mode='enforce', budget=2, retries=3, cooldown=30):
    r = client.put('/api/policy', json={'mode': mode, 'max_job_retries': retries,
        'shared_dependency_retry_budget': budget, 'dependency_cooldown_seconds': cooldown})
    assert r.status_code == 200, r.text


def run(client, key):
    r = client.post('/api/runs', json={'repository':'acme/service', 'dependency':'inventory',
        'job_id':key, 'idempotency_key':key})
    assert r.status_code == 201, r.text
    return r.json()


def fail(client, run, number=1, key=None, **kwargs):
    body = {'idempotency_key':key or f'{run["id"]}-{number}', 'attempt_number':number,
        'exit_code':1, 'duration_seconds':0.1, 'classification':'dependency',
        'evidence':'INVENTORY_UNAVAILABLE', 'health_status':'unhealthy'}
    body.update(kwargs)
    return client.post(f'/api/runs/{run["id"]}/attempts', json=body)


def test_shared_budget_atomic_across_twenty_concurrent_jobs(client):
    policy(client)
    runs = [run(client, str(i)) for i in range(20)]
    client.post('/api/dependencies/inventory/probe')
    with ThreadPoolExecutor(max_workers=20) as pool:
        responses = list(pool.map(lambda r: fail(client, r), runs))
    assert all(r.status_code == 200 for r in responses)
    actions = [r.json()['decision']['action'] for r in responses]
    assert actions.count('retry') == 2
    assert actions.count('block') == 18
    assert client.get('/api/dependencies').json()[0]['retries_used'] == 2


def test_duplicate_attempt_returns_exact_result_without_spending(client):
    policy(client)
    r = run(client, 'duplicate')
    client.post('/api/dependencies/inventory/probe')
    first = fail(client, r)
    assert first.json()['decision']['action'] == 'retry'
    assert fail(client, r).json() == first.json()
    assert client.get('/api/dependencies').json()[0]['retries_used'] == 1
    assert fail(client, r, key='different-key').status_code == 409


def test_no_pass_without_successful_attempt_or_reopening_terminal(client):
    policy(client)
    r = run(client, 'no-pass')
    url = f'/api/runs/{r["id"]}'
    assert client.post(url+'/finish', json={'status':'passed'}).status_code == 409
    assert client.post(url+'/finish', json={'status':'cancelled'}).status_code == 200
    assert fail(client, r).status_code == 409
    assert client.post(url+'/finish', json={'status':'passed'}).status_code == 409


def test_zero_budget_and_mode_switch_preserve_independent_allowances(client):
    policy(client, mode='observe', budget=1,retries=4)
    a = run(client, 'observe')
    client.post('/api/dependencies/inventory/probe')
    assert fail(client, a).json()['decision']['action'] == 'retry'
    assert fail(client, a, 2).json()['decision']['would_block'] is True
    policy(client, mode='enforce', budget=1,retries=4)
    # Already-started jobs may report an attempt after enforcement changes.
    assert fail(client, a, 3).json()['decision']['action'] == 'retry'
    policy(client, mode='enforce', budget=100,retries=4)
    assert fail(client, a, 4).json()['decision']['action'] == 'block'
    assert run(client, 'new')['decision']['action'] == 'block'
    assert client.get('/api/dependencies').json()[0]['retries_used'] == 1


def test_zero_budget_blocks_retry_but_never_invents_attempt(client):
    policy(client, budget=0)
    a = run(client, 'zero')
    client.post('/api/dependencies/inventory/probe')
    assert fail(client, a).json()['decision']['action'] == 'block'
    b = run(client, 'initial-block')
    assert b['decision']['action'] == 'block'
    assert b['attempt_count'] == 0 and b['status'] == 'blocked'


@pytest.mark.parametrize('classification,health,evidence', [
    ('assertion','unhealthy','INVENTORY_UNAVAILABLE'),
    ('unknown','unhealthy','INVENTORY_UNAVAILABLE'),
    ('timeout','unhealthy','INVENTORY_UNAVAILABLE'),
    ('dependency','unknown','INVENTORY_UNAVAILABLE'),
    ('dependency','unhealthy','some unrelated error'),
])
def test_only_confirmed_registered_dependency_failures_retry(client, classification, health, evidence):
    policy(client)
    a = run(client, classification+health+evidence)
    client.post('/api/dependencies/inventory/probe')
    result = fail(client, a, classification=classification, health_status=health, evidence=evidence)
    assert result.json()['decision']['action'] == 'stop'


def test_recovery_is_single_flight_and_requires_healthy_probe(client):
    policy(client, budget=0, cooldown=0)
    a = run(client, 'failed')
    client.post('/api/dependencies/inventory/probe')
    fail(client, a)
    first = run(client, 'recovery-owner')
    second = run(client, 'other')
    assert first['decision']['action'] == 'probe'
    assert second['decision']['action'] == 'block'
    client.app.state.health_probe = lambda dep: ('healthy', 'HTTP 200')
    body = {'run_id': first['id'], 'probe_token':first['decision']['probe_token'],
        'probe_generation':first['decision']['probe_generation']}
    recovered = client.post('/api/dependencies/inventory/probe', json=body)
    assert recovered.json()['decision']['action'] == 'run'
    assert run(client, 'after-recovery')['decision']['action'] == 'run'
    assert client.post('/api/dependencies/inventory/probe', json=body).status_code == 409


def test_persistence_and_immutable_decision_evidence(client):
    policy(client)
    a = run(client, 'history')
    original = a['policy']
    client.post('/api/dependencies/inventory/probe')
    fail(client, a)
    policy(client, mode='off')
    detail = client.get(f'/api/runs/{a["id"]}').json()
    assert detail['policy'] == original
    assert len(detail['decisions']) == 2
    assert detail['attempts'][0]['evidence'] == 'INVENTORY_UNAVAILABLE'
    module = importlib.import_module('ci_guard.app')
    restarted = TestClient(module.create_app(database_path=client.app.state.db.path))
    assert restarted.get(f'/api/runs/{a["id"]}').json()['attempt_count'] == 1


def test_auth_requires_token_unless_explicit_demo(tmp_path, monkeypatch):
    monkeypatch.delenv('CI_GUARD_DEMO_MODE', raising=False)
    monkeypatch.delenv('CI_GUARD_TOKEN', raising=False)
    module = importlib.import_module('ci_guard.app')
    c = TestClient(module.create_app(database_path=str(tmp_path/'auth.db')))
    assert c.get('/api/health').status_code == 200
    assert c.get('/api/runs').status_code == 503
    monkeypatch.setenv('CI_GUARD_TOKEN','a-test-token')
    c = TestClient(module.create_app(database_path=str(tmp_path/'auth2.db')))
    assert c.get('/api/runs').status_code == 401
    assert c.get('/api/runs', headers={'Authorization':'Bearer a-test-token'}).status_code == 200


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'http://169.254.169.254/latest',
    'http://metadata.google.internal/', 'http://user:pass@inventory/', 'http://inventory/\nheader'])
def test_dependency_urls_reject_unsafe_targets(client, url):
    assert client.post('/api/dependencies',json={'id':'bad','name':'Bad','health_url':url,
        'error_patterns':['error']}).status_code == 422


def test_off_executes_once_and_success_requires_zero_exit(client):
    policy(client, mode='off')
    a = run(client, 'off')
    result = fail(client, a)
    assert result.json()['decision']['action'] == 'stop'
    b = run(client, 'success')
    assert fail(client,b,classification='passed',exit_code=1).status_code == 422
    result = fail(client,b,classification='passed',exit_code=0)
    assert result.json()['run']['status'] == 'passed'


def test_admin_token_separates_configuration_from_runner(tmp_path, monkeypatch):
    monkeypatch.setenv('CI_GUARD_DEMO_MODE','false')
    monkeypatch.setenv('CI_GUARD_TOKEN','runner-token')
    monkeypatch.setenv('CI_GUARD_ADMIN_TOKEN','admin-token')
    module = importlib.import_module('ci_guard.app')
    client = TestClient(module.create_app(database_path=str(tmp_path/'roles.db')))
    runner_headers = {'Authorization':'Bearer runner-token'}
    admin_headers = {'Authorization':'Bearer admin-token'}
    body = {'id':'inventory','name':'Inventory','health_url':'http://inventory/health','error_patterns':['broken']}
    assert client.post('/api/dependencies',json=body,headers=runner_headers).status_code == 403
    assert client.post('/api/dependencies',json=body,headers=admin_headers).status_code == 201
    assert client.post('/api/dependencies/inventory/probe',headers=runner_headers).status_code == 403
    settings = client.get('/api/settings',headers=runner_headers)
    assert 'runner-token' not in settings.text and 'admin-token' not in settings.text


def test_policy_edit_capability_matches_authenticated_role(tmp_path, monkeypatch):
    monkeypatch.setenv('CI_GUARD_DEMO_MODE','false')
    monkeypatch.setenv('CI_GUARD_TOKEN','runner-token')
    monkeypatch.setenv('CI_GUARD_ADMIN_TOKEN','admin-token')
    monkeypatch.setenv('FLAGSMITH_ENVIRONMENT_KEY','environment-key')
    monkeypatch.setenv('FLAGSMITH_ADMIN_API_KEY','management-key')
    monkeypatch.setenv('FLAGSMITH_ENVIRONMENT_ID','environment-key')
    module = importlib.import_module('ci_guard.app')
    client = TestClient(module.create_app(database_path=str(tmp_path/'capabilities.db')))
    runner = client.get('/api/settings',headers={'Authorization':'Bearer runner-token'})
    administrator = client.get('/api/settings',headers={'Authorization':'Bearer admin-token'})
    assert runner.status_code == administrator.status_code == 200
    assert runner.json()['policy_writable'] is False
    assert administrator.json()['policy_writable'] is True


def test_unfinished_initial_probe_is_not_overwritten(client):
    from threading import Event
    started, release = Event(),Event()
    def slow_probe(dep):
        started.set()
        assert release.wait(3)
        return 'unhealthy','HTTP 503'
    client.app.state.health_probe = slow_probe
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.post,'/api/dependencies/inventory/probe')
        assert started.wait(2)
        try:
            second = client.post('/api/dependencies/inventory/probe')
            assert second.status_code == 200
            assert second.json()['performed'] is False
        finally:
            release.set()
        assert first.result().status_code == 200


def test_stale_probe_cannot_reset_later_window(client):
    from threading import Event
    from ci_guard.db import Database
    policy(client,budget=0,cooldown=0)
    a = run(client,'stale-failed')
    client.post('/api/dependencies/inventory/probe')
    fail(client,a)
    b = run(client,'stale-owner')
    started,release = Event(),Event()
    def slow_probe(dep):
        started.set()
        assert release.wait(3)
        return 'healthy','HTTP 200'
    client.app.state.health_probe = slow_probe
    body = {'run_id':b['id'],'probe_token':b['decision']['probe_token'],'probe_generation':b['decision']['probe_generation']}
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post,'/api/dependencies/inventory/probe',json=body)
        assert started.wait(2)
        with client.app.state.db.transaction() as con:
            dep = Database.get(con,'dependencies','inventory')
            dep['_generation'] += 1
            Database.save(con,'dependencies',dep)
        release.set()
        assert pending.result().status_code == 409
    assert client.get('/api/dependencies').json()[0]['window_open'] is True


def test_initial_policy_failure_executes_once_even_if_provider_recovers(client):
    from ci_guard.policy import Policy
    provider = client.app.state.policy_provider
    original = provider.evaluate
    provider.evaluate = lambda repository: Policy(mode='off',max_job_retries=0,
        shared_dependency_retry_budget=0,dependency_cooldown_seconds=30,source='flagsmith',error='Unavailable')
    a = run(client,'initial-fallback')
    provider.evaluate = original
    policy(client)
    client.post('/api/dependencies/inventory/probe')
    assert fail(client,a).json()['decision']['action'] == 'stop'


def test_secret_evidence_is_redacted_before_persistence(client, monkeypatch):
    monkeypatch.setenv('CI_GUARD_TOKEN','sensitive-runner-token')
    policy(client,mode='off')
    a = run(client,'redaction')
    fail(client,a,evidence={'log':'INVENTORY_UNAVAILABLE sensitive-runner-token password=hidden','authorization':'Bearer hidden'})
    response = client.get(f'/api/runs/{a["id"]}')
    assert 'sensitive-runner-token' not in response.text
    assert 'password=hidden' not in response.text
    assert 'Bearer hidden' not in response.text


def test_concurrent_initial_evidence_probes_share_result_without_losing_attempts(client):
    import time
    policy(client,mode='observe',budget=2)
    jobs = [run(client,f'concurrent-probe-{i}') for i in range(4)]
    calls = []
    def slow_probe(dep):
        calls.append(dep['id'])
        time.sleep(0.1)
        return 'unhealthy','HTTP 503'
    client.app.state.health_probe = slow_probe
    def job(current):
        check = client.post('/api/dependencies/inventory/probe',json={'run_id':current['id']})
        assert check.status_code == 200
        assert check.json()['status'] == 'unhealthy'
        return fail(client,current)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(job,jobs))
    assert all(result.json()['decision']['action']=='retry' for result in results)
    assert len(calls) == 1


def test_per_job_limit_does_not_invent_prevented_attempt(client):
    policy(client,retries=0,budget=100)
    a = run(client,'no-configured-retries')
    client.post('/api/dependencies/inventory/probe')
    result = fail(client,a).json()
    assert result['decision']['action'] == 'stop'
    assert result['run']['status'] == 'failed'
    assert result['run']['prevented_attempts'] == 0


def test_changed_health_target_invalidates_inflight_probe(client):
    from threading import Event
    started,release = Event(),Event()
    def probe_old_target(dep):
        started.set()
        assert release.wait(3)
        return 'unhealthy','old target failed'
    client.app.state.health_probe = probe_old_target
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post,'/api/dependencies/inventory/probe')
        assert started.wait(2)
        response = client.put('/api/dependencies/inventory',json={'id':'inventory','name':'Inventory',
            'health_url':'http://new-inventory/health','error_patterns':['INVENTORY_UNAVAILABLE']})
        release.set()
        assert response.status_code == 200
        assert pending.result().status_code == 200
    dep = client.get('/api/dependencies').json()[0]
    assert dep['status'] == 'unknown'
    assert dep['health_detail'] is None


def test_unhealthy_evidence_before_a_closed_window_must_be_recent(client):
    from ci_guard.db import Database
    policy(client)
    a = run(client,'stale-evidence')
    client.post('/api/dependencies/inventory/probe')
    with client.app.state.db.transaction() as con:
        dep = Database.get(con,'dependencies','inventory')
        dep['last_checked_at'] = '2020-01-01T00:00:00+00:00'
        Database.save(con,'dependencies',dep)
    assert fail(client,a).json()['decision']['action']=='stop'


@pytest.mark.parametrize('admission_error',[None,'Flagsmith unavailable'])
def test_recovery_admission_one_attempt_policy_survives_provider_recovery(client, admission_error):
    from ci_guard.policy import Policy
    policy(client,budget=0,cooldown=0)
    failed = run(client,'outage-before-admission')
    client.post('/api/dependencies/inventory/probe')
    assert fail(client,failed).json()['decision']['action']=='block'
    owner = run(client,'recovery-one-attempt')
    assert owner['decision']['action']=='probe'

    provider = client.app.state.policy_provider
    original_evaluate = provider.evaluate
    provider.evaluate = lambda repository: Policy(mode='off',max_job_retries=0,
        shared_dependency_retry_budget=0,source='flagsmith',error=admission_error)
    client.app.state.health_probe = lambda dep: ('healthy','HTTP 200')
    admitted = client.post('/api/dependencies/inventory/probe',json={
        'run_id':owner['id'],'probe_token':owner['decision']['probe_token'],
        'probe_generation':owner['decision']['probe_generation']})
    assert admitted.status_code == 200
    assert admitted.json()['decision']['action']=='run'
    assert admitted.json()['decision']['policy']['mode']=='off'

    provider.evaluate = original_evaluate
    policy(client,budget=2,cooldown=0)
    client.app.state.health_probe = lambda dep: ('unhealthy','HTTP 503')
    assert client.post('/api/dependencies/inventory/probe',json={'run_id':owner['id']}).json()['status']=='unhealthy'
    result = fail(client,owner).json()
    assert result['decision']['action']=='stop'
    assert result['run']['status']=='failed'
    assert result['run']['attempt_count']==1
    assert client.get('/api/dependencies').json()[0]['retries_used']==0


@pytest.mark.parametrize('cooldown,active_lease,expected_would_block',[(0,False,False),(30,False,True),(0,True,True)])
def test_observe_admission_counts_only_deterministic_enforcement_blocks(client,cooldown,active_lease,expected_would_block):
    policy(client,budget=0,cooldown=cooldown)
    initial = run(client,'observe-admission-outage')
    client.post('/api/dependencies/inventory/probe')
    assert fail(client,initial).json()['decision']['action']=='block'
    if active_lease:
        assert run(client,'observe-admission-probe-owner')['decision']['action']=='probe'
    policy(client,mode='observe',budget=0,cooldown=cooldown)
    observed = run(client,'observe-admission-shadow')
    assert observed['decision']['action']=='run'
    assert observed['decision']['would_block'] is expected_would_block
    assert observed['would_prevent_attempts']==int(expected_would_block)
    assert observed['prevented_attempts']==0
