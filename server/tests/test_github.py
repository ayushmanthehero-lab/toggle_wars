"""Real SQLite/API behavior with only GitHub's HTTP boundary simulated."""
import importlib
import importlib.util
import json
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from ci_guard.db import Database


ADMIN = {'Authorization': 'Bearer admin-secret'}
ACCOUNT = {'id': 12, 'login': 'octocat', 'avatar_url': 'https://avatars.githubusercontent.com/u/12',
           'html_url': 'https://github.com/octocat'}
REPO = {'id': 42, 'full_name': 'octocat/hello', 'name': 'hello', 'private': True,
        'default_branch': 'main', 'html_url': 'https://github.com/octocat/hello', 'description': 'Tests'}


@pytest.fixture
def system(tmp_path, monkeypatch):
    assert importlib.util.find_spec('ci_guard.github'), 'GitHub service has not been implemented'
    from cryptography.fernet import Fernet
    module = importlib.import_module('ci_guard.github')
    monkeypatch.setenv('CI_GUARD_GITHUB_ENCRYPTION_KEY', Fernet.generate_key().decode())
    monkeypatch.setenv('CI_GUARD_GITHUB_CLIENT_ID', 'Iv1.public-client')
    monkeypatch.setenv('CI_GUARD_GITHUB_APP_SLUG', 'ci-guard-test')
    monkeypatch.delenv('CI_GUARD_GITHUB_TOKEN', raising=False)
    db = Database(tmp_path / 'guard.db')
    app = FastAPI()
    def strict_admin(authorization: str | None = Header(default=None)):
        if authorization != ADMIN['Authorization']:
            raise HTTPException(401, 'Administrator token required')
    store = module.install_github_routes(app, db, strict_admin)
    calls = []
    responses = {}
    def handler(request):
        calls.append(request)
        value = responses.get((request.method, request.url.path))
        if callable(value):
            return value(request)
        if value is not None:
            return httpx.Response(200, json=value)
        if request.url.path == '/user':
            return httpx.Response(200, json=ACCOUNT)
        if request.url.path == '/user/repos':
            return httpx.Response(200, json=[REPO])
        if request.url.path == '/repos/octocat/hello':
            return httpx.Response(200, json=REPO)
        raise AssertionError(f'Unexpected GitHub request: {request.method} {request.url.path}')
    store.client = module.GitHubClient(transport=httpx.MockTransport(handler))
    clock = [1000.0]
    monkeypatch.setattr(module.time, 'time', lambda: clock[0])
    with TestClient(app) as client:
        yield client, store, db, responses, calls, clock


def connect(client):
    result = client.post('/api/github/connect/token', headers=ADMIN, json={'token': 'github-secret-value'})
    assert result.status_code == 201, result.text
    return result.json()


def project(client, connection):
    result = client.post('/api/projects', headers=ADMIN,
                         json={'connection_id': connection['id'], 'repository_id': REPO['id']})
    assert result.status_code == 201, result.text
    return result.json()


def test_github_admin_boundary_never_uses_demo_bypass(system):
    client, *_ = system
    for url in ['/api/github/status', '/api/projects']:
        assert client.get(url).status_code == 401
        assert client.get(url, headers={'Authorization': 'Bearer runner-token'}).status_code == 401
        assert client.get(url, headers=ADMIN).status_code == 200


def test_token_connection_encrypts_secret_and_returns_only_public_fields(system):
    client, store, db, *_ = system
    connection = connect(client)
    assert connection['login'] == 'octocat'
    assert connection['auth_type'] == 'token'
    assert 'github-secret-value' not in json.dumps(client.get('/api/github/status', headers=ADMIN).json())
    with db.transaction() as con:
        record = dict(con.execute('SELECT * FROM github_connections').fetchone())
        assert con.execute("SELECT data FROM configuration WHERE key='github_auth_enforced'").fetchone()[0] == 'true'
    assert 'github-secret-value' not in json.dumps(record)
    assert store.credential(connection['id']) == 'github-secret-value'


def test_environment_opt_in_never_falls_back_to_gh_token(system, monkeypatch):
    client, store, db, *_ = system
    monkeypatch.setenv('GH_TOKEN', 'incidental-secret')
    assert client.get('/api/github/status', headers=ADMIN).json()['environment_token_available'] is False
    assert client.post('/api/github/connect/environment', headers=ADMIN).status_code == 503
    monkeypatch.setenv('CI_GUARD_GITHUB_TOKEN', 'explicit-secret')
    response = client.post('/api/github/connect/environment', headers=ADMIN)
    assert response.status_code == 201
    assert store.credential(response.json()['id']) == 'explicit-secret'
    with db.transaction() as con:
        assert con.execute('SELECT token_ciphertext FROM github_connections').fetchone()[0] is None


def test_missing_key_does_not_store_plaintext_or_call_github(system, monkeypatch):
    client, _, db, _, calls, _ = system
    monkeypatch.delenv('CI_GUARD_GITHUB_ENCRYPTION_KEY')
    assert client.post('/api/github/connect/token', headers=ADMIN, json={'token': 'sensitive'}).status_code == 503
    assert not calls
    with db.transaction() as con:
        assert con.execute('SELECT COUNT(*) FROM github_connections').fetchone()[0] == 0


def test_project_selection_requires_actual_access_and_runner_rotation_revokes(system):
    client, store, db, *_ = system
    connection = connect(client)
    denied = client.post('/api/projects', headers=ADMIN, json={'connection_id': connection['id'], 'repository_id': 99})
    assert denied.status_code == 403
    selected = project(client, connection)
    with db.transaction() as con:
        con.execute('INSERT INTO dependencies VALUES (?, ?)', ('inventory', '{}'))
    url = f"/api/projects/{selected['id']}/runner-token"
    assert client.post(url, headers=ADMIN, json={'dependency_ids': ['missing']}).status_code == 422
    issued = client.post(url, headers=ADMIN, json={'dependency_ids': ['inventory']}).json()
    assert issued['token'].startswith('cg_proj_')
    assert store.authenticate_project_token(issued['token']) == {
        'project_id': selected['id'], 'repository': 'octocat/hello', 'dependency_ids': ['inventory']}
    replacement = client.post(url, headers=ADMIN, json={'dependency_ids': ['inventory']}).json()
    assert store.authenticate_project_token(issued['token']) is None
    with db.transaction() as con:
        assert replacement['token'] not in json.dumps(dict(con.execute('SELECT * FROM github_project_tokens').fetchone()))
    assert client.delete(f"/api/github/connections/{connection['id']}", headers=ADMIN).status_code == 204
    assert store.authenticate_project_token(replacement['token']) is None
    assert client.get('/api/projects', headers=ADMIN).json() == []


def test_device_poll_is_interval_limited_encrypted_and_single_use(system):
    client, store, db, responses, calls, clock = system
    responses['POST', '/login/device/code'] = {'device_code': 'private-device-secret', 'user_code': 'ABCD-EFGH',
        'verification_uri': 'https://github.com/login/device', 'expires_in': 900, 'interval': 5}
    result = client.post('/api/github/device/start', headers=ADMIN)
    assert result.status_code == 201, result.text
    device = result.json()
    assert 'private-device-secret' not in result.text
    assert parse_qs(calls[-1].content.decode()) == {'client_id': ['Iv1.public-client']}
    with db.transaction() as con:
        assert 'private-device-secret' not in con.execute('SELECT data FROM github_devices').fetchone()[0]
    responses['POST', '/login/oauth/access_token'] = {'error': 'authorization_pending'}
    poll = lambda: client.post('/api/github/device/poll', headers=ADMIN, json={'id': device['id']})
    assert poll().json()['status'] == 'pending'
    assert len(calls) == 1
    clock[0] += 5
    assert poll().json()['status'] == 'pending'
    assert parse_qs(calls[-1].content.decode())['grant_type'] == ['urn:ietf:params:oauth:grant-type:device_code']
    responses['POST', '/login/oauth/access_token'] = {'error': 'slow_down'}
    clock[0] += 5
    assert poll().json()['interval'] == 10
    before = len(calls)
    clock[0] += 5
    assert poll().json()['status'] == 'pending'
    assert len(calls) == before
    responses['POST', '/login/oauth/access_token'] = {'access_token': 'device-user-secret', 'expires_in': 3600,
                                                   'refresh_token': 'never-store-refresh'}
    clock[0] += 5
    connected = poll()
    assert connected.json()['status'] == 'connected', connected.text
    assert connected.json()['connection']['auth_type'] == 'device'
    assert poll().status_code == 409
    with db.transaction() as con:
        saved = [dict(row) for row in con.execute('SELECT * FROM github_connections')]
        assert 'never-store-refresh' not in json.dumps(saved)


def test_device_expiry_and_denial_stop_remote_polling(system):
    client, _, _, responses, calls, clock = system
    responses['POST', '/login/device/code'] = {'device_code': 'secret-code', 'user_code': 'ABCD-EFGH',
        'verification_uri': 'https://github.com/login/device', 'expires_in': 10, 'interval': 5}
    device = client.post('/api/github/device/start', headers=ADMIN).json()
    clock[0] += 11
    assert client.post('/api/github/device/poll', headers=ADMIN, json={'id': device['id']}).json()['status'] == 'expired'
    assert len(calls) == 1
    device = client.post('/api/github/device/start', headers=ADMIN).json()
    responses['POST', '/login/oauth/access_token'] = {'error': 'access_denied'}
    clock[0] += 5
    assert client.post('/api/github/device/poll', headers=ADMIN, json={'id': device['id']}).json()['status'] == 'denied'


def test_device_lists_only_this_apps_authorized_installations(system):
    client, store, _, responses, calls, _ = system
    connection = store.connect('device-user-token', source='device')
    responses['GET', '/user/installations'] = {'installations': [
        {'id': 5, 'app_slug': 'ci-guard-test'}, {'id': 6, 'app_slug': 'another-app'}]}
    responses['GET', '/user/installations/5/repositories'] = {'repositories': [REPO]}
    result = client.get(f"/api/github/connections/{connection['id']}/repositories", headers=ADMIN)
    assert result.json() == [REPO]
    assert not any('/6/' in str(request.url) for request in calls)


def test_dispatch_checks_repo_and_workflow_and_never_retries_ambiguous_write(system):
    client, _, _, responses, calls, _ = system
    selected = project(client, connect(client))
    responses['GET', '/repos/octocat/hello/actions/workflows/7'] = {'id': 7, 'state': 'active'}
    dispatch_path = '/repos/octocat/hello/actions/workflows/7/dispatches'
    responses['POST', dispatch_path] = lambda request: httpx.Response(204)
    url = f"/api/projects/{selected['id']}/dispatch"
    result = client.post(url, headers=ADMIN, json={'workflow_id': 7, 'ref': 'main', 'inputs': {'mode': 'demo'}})
    assert result.json() == {'status': 'accepted', 'html_url': 'https://github.com/octocat/hello/actions'}
    assert sum(request.url.path == dispatch_path for request in calls) == 1
    def lost(request):
        raise httpx.ReadTimeout('SECRET-IN-REMOTE-ERROR', request=request)
    responses['POST', dispatch_path] = lost
    result = client.post(url, headers=ADMIN, json={'workflow_id': 7, 'ref': 'main'})
    assert result.status_code == 502
    assert 'SECRET-IN-REMOTE-ERROR' not in result.text
    assert sum(request.url.path == dispatch_path for request in calls) == 2


def test_expired_connection_requires_reconnect_without_environment_fallback(system, monkeypatch):
    client, store, _, _, calls, clock = system
    connection = store.connect('expiring-token', source='device', expires_in=1)
    monkeypatch.setenv('CI_GUARD_GITHUB_TOKEN', 'different-account-token')
    clock[0] += 2
    before = len(calls)
    result = client.get(f"/api/github/connections/{connection['id']}/repositories", headers=ADMIN)
    assert result.status_code == 401
    assert 'reconnect' in result.text.lower()
    assert len(calls) == before


def test_reconnect_preserves_project_and_environment_account_swap_is_rejected(system, monkeypatch):
    client, _, _, responses, _, _ = system
    connection = connect(client)
    selected = project(client, connection)
    again = client.post('/api/github/connect/token', headers=ADMIN, json={'token': 'replacement-secret'}).json()
    assert again['id'] == connection['id']
    assert client.get('/api/projects', headers=ADMIN).json()[0]['id'] == selected['id']
    monkeypatch.setenv('CI_GUARD_GITHUB_TOKEN', 'original-environment-secret')
    environment = client.post('/api/github/connect/environment', headers=ADMIN).json()
    monkeypatch.setenv('CI_GUARD_GITHUB_TOKEN', 'another-account-secret')
    responses['GET', '/user'] = {**ACCOUNT, 'id': 99, 'login': 'someone-else'}
    result = client.get(f"/api/github/connections/{environment['id']}/repositories", headers=ADMIN)
    assert result.status_code == 401
    assert 'identity changed' in result.text


def test_workflow_and_run_views_preserve_safe_github_urls_and_strip_extras(system):
    client, _, _, responses, _, _ = system
    selected = project(client, connect(client))
    workflow = {'id': 7, 'name': 'Tests', 'path': '.github/workflows/tests.yml', 'state': 'active',
                'html_url': 'https://github.com/octocat/hello/blob/main/.github/workflows/tests.yml', 'secret': 'omit'}
    responses['GET', '/repos/octocat/hello/actions/workflows'] = {'workflows': [workflow]}
    response = client.get(f"/api/projects/{selected['id']}/workflows", headers=ADMIN)
    assert response.status_code == 200
    assert response.json() == [{key: value for key, value in workflow.items() if key != 'secret'}]
    responses['GET', '/repos/octocat/hello/actions/runs'] = {'workflow_runs': [
        {'id': 10, 'name': 'Tests', 'status': 'completed', 'conclusion': 'success',
         'head_branch': 'main', 'event': 'workflow_dispatch', 'created_at': 'date', 'updated_at': 'date',
         'run_number': 1, 'html_url': 'https://untrusted.example/steal', 'extra': 'omit'}]}
    runs = client.get(f"/api/projects/{selected['id']}/actions/runs", headers=ADMIN).json()
    assert runs[0]['html_url'] == 'https://github.com/octocat/hello/actions/runs/10'
    assert 'extra' not in runs[0]


@pytest.mark.parametrize('resource,payload', [
    ('workflows', {'workflows': ['unexpected']}),
    ('actions/runs', {'workflow_runs': ['unexpected']}),
])
def test_invalid_remote_list_entries_have_safe_error(system, resource, payload):
    client, _, _, responses, _, _ = system
    selected = project(client, connect(client))
    endpoint = '/repos/octocat/hello/actions/' + ('workflows' if resource == 'workflows' else 'runs')
    responses['GET', endpoint] = payload
    result = client.get(f"/api/projects/{selected['id']}/{resource}", headers=ADMIN)
    assert result.status_code == 502


def test_project_deletion_revokes_token_and_preserves_other_projects(system):
    client, store, db, *_ = system
    selected = project(client, connect(client))
    with db.transaction() as con:
        con.execute('INSERT INTO dependencies VALUES (?,?)', ('inventory', '{}'))
    token = store.rotate_runner_token(selected['id'], ['inventory'])['token']
    assert client.delete(f"/api/projects/{selected['id']}", headers=ADMIN).status_code == 204
    assert store.authenticate_project_token(token) is None
    assert client.get('/api/projects', headers=ADMIN).json() == []
    assert len(client.get('/api/github/status', headers=ADMIN).json()['connections']) == 1


def test_removed_app_installation_blocks_existing_project_remote_access(system):
    client, store, _, responses, _, _ = system
    connection = store.connect('app-user-token', source='device')
    responses['GET', '/user/installations'] = {'installations': [{'id': 5, 'app_slug': 'ci-guard-test'}]}
    responses['GET', '/user/installations/5/repositories'] = {'repositories': [REPO]}
    selected = project(client, connection)
    # Public metadata may remain visible to an App user token after installation
    # removal; a selected project must still honor the app's actual access list.
    responses['GET', '/user/installations'] = {'installations': []}
    responses['GET', '/repos/octocat/hello/actions/workflows'] = {'workflows': []}
    response = client.get(f"/api/projects/{selected['id']}/workflows", headers=ADMIN)
    assert response.status_code == 403


def test_invalid_installation_response_is_safe(system):
    client, store, _, responses, _, _ = system
    connection = store.connect('app-user-token', source='device')
    responses['GET', '/user/installations'] = {'installations': ['bad-entry']}
    response = client.get(f"/api/github/connections/{connection['id']}/repositories", headers=ADMIN)
    assert response.status_code == 502
