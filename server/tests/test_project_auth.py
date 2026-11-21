"""Real SQLite/API isolation checks for repository-scoped runner credentials."""
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from ci_guard.db import Database, dump


ADMIN = {'Authorization':'Bearer project-test-admin'}


def headers(token):
    return {'Authorization':'Bearer '+token}


@pytest.fixture
def workspace(tmp_path,monkeypatch):
    monkeypatch.setenv('CI_GUARD_DEMO_MODE','true')
    monkeypatch.setenv('CI_GUARD_TOKEN','legacy-runner')
    monkeypatch.setenv('CI_GUARD_ADMIN_TOKEN','project-test-admin')
    monkeypatch.delenv('FLAGSMITH_ENVIRONMENT_KEY',raising=False)
    app = importlib.import_module('ci_guard.app').create_app(database_path=str(tmp_path/'projects.db'))
    app.state.health_probe = lambda dependency: ('unhealthy','HTTP 503')
    client = TestClient(app)
    for dep in ('inventory','billing'):
        response = client.post('/api/dependencies',headers=ADMIN,json={'id':dep,'name':dep,
            'health_url':f'http://{dep}/health','error_patterns':['DEPENDENCY_DOWN']})
        assert response.status_code == 201,response.text
    with app.state.db.transaction() as con:
        connection = {'id':'connection','login':'team','avatar_url':'https://avatars.githubusercontent.com/u/1',
            'html_url':'https://github.com/team','auth_type':'token','created_at':'2026-11-21T00:00:00+00:00'}
        con.execute('INSERT INTO github_connections VALUES (?,?,?,?,?)',('connection',dump(connection),None,'environment',None))
        for identifier,repository,repo_id in [('one','team/one',1),('two','team/two',2)]:
            project = {'id':identifier,'repository_id':repo_id,'connection_id':'connection','full_name':repository,
                'name':identifier,'private':False,'default_branch':'main','html_url':'https://github.com/'+repository,
                'created_at':'2026-11-21T00:00:00+00:00'}
            con.execute('INSERT INTO github_projects VALUES (?,?,?,?)',(identifier,'connection',repo_id,dump(project)))
    tokens = {identifier:app.state.github_store.rotate_runner_token(identifier,['inventory'])['token'] for identifier in ('one','two')}
    return client,tokens


def create_run(client,token,repository='team/one',dependency='inventory',key='job'):
    return client.post('/api/runs',headers=headers(token),json={'repository':repository,
        'dependency':dependency,'job_id':key,'idempotency_key':key})


def attempt(client,token,run_id):
    return client.post(f'/api/runs/{run_id}/attempts',headers=headers(token),json={
        'idempotency_key':'first','attempt_number':1,'exit_code':1,'duration_seconds':0.1,
        'classification':'dependency','evidence':'DEPENDENCY_DOWN','health_status':'unhealthy'})


def test_schema_upgrade_preserves_existing_run_and_policy(tmp_path):
    path = tmp_path/'v1.db'
    db = Database(path)
    with db.transaction() as con:
        con.execute('INSERT INTO runs VALUES (?,?,?,?)',('historical','history','hash',dump({'id':'historical','status':'failed'})))
        con.execute("INSERT INTO configuration VALUES ('demo_policy',?)",(dump({'mode':'observe'}),))
        for table in ('github_project_tokens','github_devices','github_projects','github_connections'):
            con.execute(f'DROP TABLE IF EXISTS {table}')
        con.execute('PRAGMA user_version=1')
    migrated = Database(path)
    with migrated.transaction() as con:
        assert con.execute('PRAGMA user_version').fetchone()[0]==2
        assert json.loads(con.execute("SELECT data FROM runs WHERE id='historical'").fetchone()[0])['status']=='failed'
        assert con.execute('SELECT count(*) FROM github_project_tokens').fetchone()[0]==0
    assert migrated.get_demo_policy()=={'mode':'observe'}


def test_project_token_cannot_spoof_repository_or_dependency_even_in_demo(workspace):
    client,tokens = workspace
    assert create_run(client,tokens['one']).status_code==201
    assert create_run(client,tokens['one'],repository='team/two',key='spoof').status_code==403
    assert create_run(client,tokens['one'],dependency='billing',key='billing').status_code==403
    assert create_run(client,tokens['one'],key='case',repository='TEAM/ONE').json()['repository']=='team/one'
    assert client.get('/api/runs',headers=headers('cg_proj_invalid')).status_code==401


def test_run_history_and_mutations_are_project_scoped(workspace):
    client,tokens = workspace
    one = create_run(client,tokens['one'],key='same-key').json()
    two_response = create_run(client,tokens['two'],repository='team/two',key='same-key')
    assert two_response.status_code==201,two_response.text
    two = two_response.json()
    legacy = create_run(client,'legacy-runner',key='legacy-same-repository').json()
    visible = client.get('/api/runs',headers=headers(tokens['one'])).json()
    assert [run['id'] for run in visible]==[one['id']]
    assert client.get('/api/runs?repository=team/two',headers=headers(tokens['one'])).status_code==403
    for foreign in (two,legacy):
        url = f'/api/runs/{foreign["id"]}'
        assert client.get(url,headers=headers(tokens['one'])).status_code==404
        assert client.post(url+'/finish',headers=headers(tokens['one']),json={'status':'cancelled'}).status_code==404
        assert attempt(client,tokens['one'],foreign['id']).status_code==404
        assert client.post('/api/dependencies/inventory/probe',headers=headers(tokens['one']),json={'run_id':foreign['id']}).status_code==404
    assert client.get(f'/api/runs/{one["id"]}',headers=headers(tokens['one'])).status_code==200


def test_dependencies_policy_and_administration_are_scoped(workspace):
    client,tokens = workspace
    scope = headers(tokens['one'])
    deps = client.get('/api/dependencies',headers=scope)
    assert [dep['id'] for dep in deps.json()]==['inventory']
    assert client.get('/api/policy?repository=team/two',headers=scope).status_code==403
    evaluated = []
    original = client.app.state.policy_provider.evaluate
    def capture(repository=''):
        evaluated.append(repository)
        return original(repository)
    client.app.state.policy_provider.evaluate = capture
    assert client.get('/api/policy',headers=scope).status_code==200
    assert evaluated==['team/one']
    assert client.post('/api/dependencies/inventory/probe',headers=scope).status_code==403
    own = create_run(client,tokens['one']).json()
    assert client.post('/api/dependencies/billing/probe',headers=scope,json={'run_id':own['id']}).status_code==403
    for path in ('/api/audit','/api/settings','/api/overview','/api/projects','/api/github/status'):
        assert client.get(path,headers=scope).status_code in (401,403)
    assert client.put('/api/policy',headers=scope,json={'mode':'off','max_job_retries':0,
        'shared_dependency_retry_budget':0,'dependency_cooldown_seconds':0}).status_code==403


def test_rotation_and_disconnect_revoke_tokens_without_deleting_history(workspace):
    client,tokens = workspace
    own = create_run(client,tokens['one']).json()
    rotated = client.post('/api/projects/one/runner-token',headers=ADMIN,json={'dependency_ids':['inventory']})
    assert rotated.status_code in (200,201),rotated.text
    fresh = rotated.json()['token']
    assert fresh.startswith('cg_proj_') and fresh!=tokens['one']
    assert client.get('/api/runs',headers=headers(tokens['one'])).status_code==401
    assert client.get('/api/runs',headers=headers(fresh)).status_code==200
    with client.app.state.db.transaction() as con:
        hashes = con.execute('SELECT token_hash FROM github_project_tokens').fetchall()
    assert all(row[0]!=fresh and not row[0].startswith('cg_proj_') for row in hashes)
    assert client.delete('/api/github/connections/connection',headers=ADMIN).status_code in (200,204)
    assert client.get('/api/runs',headers=headers(fresh)).status_code==401
    assert client.get(f'/api/runs/{own["id"]}',headers=ADMIN).status_code==200
    assert client.get('/api/runs').status_code==401


def test_project_tokens_share_the_same_global_dependency_allowance(workspace):
    client,tokens = workspace
    assert client.put('/api/policy',headers=ADMIN,json={'mode':'enforce','max_job_retries':2,
        'shared_dependency_retry_budget':1,'dependency_cooldown_seconds':30}).status_code==200
    first = create_run(client,tokens['one']).json()
    second = create_run(client,tokens['two'],repository='team/two').json()
    assert client.post('/api/dependencies/inventory/probe',headers=headers(tokens['one']),json={'run_id':first['id']}).json()['status']=='unhealthy'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda values:attempt(client,*values),[(tokens['one'],first['id']),(tokens['two'],second['id'])]))
    assert sorted(result.json()['decision']['action'] for result in results)==['block','retry']
    assert client.get('/api/dependencies',headers=ADMIN).json()[1]['retries_used']==1


def test_real_connections_permanently_disable_anonymous_demo_access(workspace):
    client,tokens = workspace
    for path in ('/api/runs','/api/overview','/api/dependencies','/api/settings'):
        assert client.get(path).status_code==401
    assert client.get('/api/settings',headers=ADMIN).json()['auth_required'] is True
    assert client.delete('/api/github/connections/connection',headers=ADMIN).status_code in (200,204)
    assert client.get('/api/runs').status_code==401


def test_github_administration_requires_actual_admin_before_demo_connect(tmp_path,monkeypatch):
    monkeypatch.setenv('CI_GUARD_DEMO_MODE','true')
    monkeypatch.setenv('CI_GUARD_ADMIN_TOKEN','project-test-admin')
    app = importlib.import_module('ci_guard.app').create_app(database_path=str(tmp_path/'empty.db'))
    client = TestClient(app)
    assert client.get('/api/runs').status_code==200
    assert client.get('/api/github/status').status_code==401
    assert client.get('/api/projects',headers=headers('cg_proj_invalid')).status_code==401
    assert client.get('/api/github/status',headers=ADMIN).status_code==200


def test_rotated_dependency_scope_hides_previous_disallowed_runs(workspace):
    client,tokens = workspace
    own = create_run(client,tokens['one']).json()
    fresh = client.app.state.github_store.rotate_runner_token('one',['billing'])['token']
    assert client.get('/api/runs',headers=headers(fresh)).json()==[]
    assert client.get(f'/api/runs/{own["id"]}',headers=headers(fresh)).status_code==404
    assert create_run(client,fresh,key='old-dependency').status_code==403
    assert create_run(client,fresh,dependency='billing',key='new-dependency').status_code==201


def test_project_bearer_is_redacted_from_recorded_evidence(workspace):
    client,tokens = workspace
    own = create_run(client,tokens['one']).json()
    response = client.post(f'/api/runs/{own["id"]}/attempts',headers=headers(tokens['one']),json={
        'idempotency_key':'first','attempt_number':1,'exit_code':1,'duration_seconds':0.1,
        'classification':'assertion','evidence':'Debug credential '+tokens['one'],'health_status':'unknown'})
    assert response.status_code==200
    recorded = client.get(f'/api/runs/{own["id"]}',headers=headers(tokens['one']))
    assert tokens['one'] not in recorded.text
    assert '[REDACTED]' in recorded.text


def test_project_removal_revokes_token_and_preserves_admin_history(workspace):
    client,tokens = workspace
    own = create_run(client,tokens['one']).json()
    assert client.delete('/api/projects/one',headers=ADMIN).status_code in (200,204)
    assert client.get('/api/runs',headers=headers(tokens['one'])).status_code==401
    assert client.get(f'/api/runs/{own["id"]}',headers=ADMIN).status_code==200


def test_readiness_supports_admin_and_project_tokens_without_legacy_runner(tmp_path,monkeypatch):
    from ci_guard.policy import Policy
    monkeypatch.setenv('CI_GUARD_DEMO_MODE','false')
    monkeypatch.delenv('CI_GUARD_TOKEN',raising=False)
    monkeypatch.setenv('CI_GUARD_ADMIN_TOKEN','project-test-admin')
    app = importlib.import_module('ci_guard.app').create_app(database_path=str(tmp_path/'admin-only.db'))
    app.state.policy_provider.evaluate = lambda repository: Policy(mode='enforce',source='flagsmith')
    assert TestClient(app).get('/api/ready').status_code==200
