"""Verify actual subprocess demos against a running local coordinator and Flagsmith.

This changes the controlled demo dependency and temporarily changes environment
policy, restoring its original values at the end. It never contacts GitHub.
"""
import argparse
import json
import os
import time
from pathlib import Path
import httpx

FIELDS = ('mode','max_job_retries','shared_dependency_retry_budget','dependency_cooldown_seconds')

def verify(url: str):
    results=[]
    token=os.environ.get('CI_GUARD_ADMIN_TOKEN','')
    configuration=Path(__file__).resolve().parents[1]/'.local/runtime.env'
    if not token and configuration.exists():
        values=dict(line.split('=',1) for line in configuration.read_text().splitlines() if '=' in line and not line.startswith('#'))
        token=values.get('CI_GUARD_ADMIN_TOKEN','')
    headers={'Authorization':f'Bearer {token}'} if token else {}
    with httpx.Client(base_url=url,headers=headers,timeout=120,trust_env=False) as client:
        original=client.get('/api/policy').raise_for_status().json()
        assert original['source']=='flagsmith' and not original.get('error'), original
        def policy(mode):
            response=client.put('/api/policy',json=dict(mode=mode,max_job_retries=2,
                shared_dependency_retry_budget=2,dependency_cooldown_seconds=1))
            response.raise_for_status()
            data=response.json()
            assert data['mode']==mode and data['source']=='flagsmith' and not data.get('error')
        def run(scenario,jobs,expected):
            response=client.post('/api/demo/run',json={'scenario':scenario,'jobs':jobs})
            response.raise_for_status()
            payload=response.json()
            summaries=[]
            for entry in payload['results']:
                report=entry['report']
                assert report['status']==expected, report.get('reason')
                assert not report.get('coordination_error'), report.get('coordination_error')
                assert (entry['exit_code']==0)==(expected=='passed')
                stored=client.get('/api/runs/'+report['run_id']).raise_for_status().json()
                assert stored['attempt_count']==len(report['attempts'])
                summaries.append({'status':report['status'],'attempts':len(report['attempts']),
                    'seconds':report['actual_execution_seconds'],'run_id':report['run_id']})
            result={'scenario':scenario,'mode':client.get('/api/policy').json()['mode'],'jobs':summaries,
                    'attempts':sum(x['attempts'] for x in summaries),
                    'execution_seconds':round(sum(x['seconds'] for x in summaries),3)}
            results.append(result)
            print(json.dumps({k:v for k,v in result.items() if k!='jobs'}),flush=True)
            return result
        try:
            policy('observe')
            # Wait only for a pre-existing short demo cooldown. Existing long windows
            # must be recovered manually instead of silently resetting database state.
            deps=client.get('/api/dependencies').json()
            if any(x['id']=='inventory-api' for x in deps):
                httpx.post('http://127.0.0.1:8090/__control',json={'healthy':True},timeout=5,trust_env=False).raise_for_status()
                probe=client.post('/api/dependencies/inventory-api/probe',json={}).raise_for_status().json()
                assert probe['status']=='healthy','Existing cooldown active; recover dependency before running this verification.'
            assert run('healthy',1,'passed')['attempts']==1
            observed=run('outage',4,'failed')
            assert observed['attempts']==12, observed
            time.sleep(1.1)
            assert run('healthy',1,'passed')['attempts']==1
            policy('enforce')
            protected=run('outage',4,'blocked')
            assert 1<=protected['attempts']<=6, protected
            assert protected['attempts']<observed['attempts']
            # Immediate repeat remains non-successful; no claimed passed skipped suite.
            repeat=run('outage',4,'blocked')
            assert repeat['attempts']==0,repeat
            time.sleep(1.1)
            assert run('healthy',1,'passed')['attempts']==1
            bug=run('assertion',2,'failed')
            assert bug['attempts']==2,bug
            assert run('healthy',2,'passed')['attempts']==2
        finally:
            client.put('/api/policy',json={key:original[key] for key in FIELDS}).raise_for_status()
    output=Path('.local/verification-demo.json')
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps({'verified_at':time.time(),'runs':results},indent=2))
    print('Verified actual Flagsmith policy changes, shared retries, blocked reruns, assertion failure, and recovery. Original policy restored.')

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--server',default='http://127.0.0.1:8080')
    verify(parser.parse_args().server)
