"""Administrator-only GitHub connections and selected repository projects.

Only ciphertext or token hashes are persisted. External requests always pass
through the fixed-origin GitHub client; connection responses are explicit views.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import re
import secrets
import time
from urllib.parse import urlsplit
import uuid

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .db import dump
from .github_client import GitHubClient


CONNECTION_FIELDS = ('id', 'login', 'avatar_url', 'html_url', 'auth_type', 'created_at', 'expires_at')
REPOSITORY_NAME = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z')
APP_SLUG = re.compile(r'[a-zA-Z0-9-]+\Z')


def iso(timestamp=None):
    return datetime.fromtimestamp(time.time() if timestamp is None else timestamp, timezone.utc).isoformat()


def _positive_id(value):
    if type(value) is not int or value <= 0:
        raise HTTPException(502, 'GitHub returned an invalid identifier.')
    return value


def _cipher():
    try:
        return Fernet(os.environ.get('CI_GUARD_GITHUB_ENCRYPTION_KEY', '').encode())
    except (ValueError, TypeError):
        raise HTTPException(503, 'Configure a valid CI_GUARD_GITHUB_ENCRYPTION_KEY before connecting GitHub.') from None


def _encrypt(value):
    return _cipher().encrypt(value.encode()).decode()


def _decrypt(value):
    try:
        return _cipher().decrypt(value.encode()).decode()
    except (InvalidToken, UnicodeError, AttributeError):
        raise HTTPException(503, 'Stored GitHub credentials cannot be decrypted. Restore the deployment key or reconnect.') from None


def _repository(value):
    if not isinstance(value, dict) or not isinstance(value.get('full_name'), str) or not REPOSITORY_NAME.fullmatch(value['full_name']):
        raise HTTPException(502, 'GitHub returned an invalid repository.')
    full_name = value['full_name']
    if any(part in ('.', '..') for part in full_name.split('/')):
        raise HTTPException(502, 'GitHub returned an invalid repository name.')
    return {'id': _positive_id(value.get('id')), 'full_name': full_name,
            'name': full_name.split('/')[1], 'private': bool(value.get('private')),
            'default_branch': str(value.get('default_branch') or 'main')[:255],
            'html_url': 'https://github.com/' + full_name,
            'description': str(value['description'])[:2000] if value.get('description') is not None else None}


def _github_link(value, repository_url, fallback):
    if isinstance(value, str):
        try:
            parsed = urlsplit(value)
            if (parsed.scheme == 'https' and parsed.netloc == 'github.com' and not parsed.query
                    and not parsed.fragment and value.startswith(repository_url + '/')):
                return value
        except ValueError:
            pass
    return fallback


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class TokenInput(Input):
    token: SecretStr = Field(min_length=1, max_length=4096)


class DevicePollInput(Input):
    id: str = Field(pattern=r'^[a-f0-9]{32}$')


class ProjectInput(Input):
    connection_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    repository_id: int = Field(gt=0, strict=True)


class RunnerTokenInput(Input):
    dependency_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator('dependency_ids')
    @classmethod
    def valid_dependencies(cls, value):
        if any(not item or len(item) > 200 or any(ord(c) < 32 for c in item) for item in value):
            raise ValueError('Use valid registered dependency identifiers')
        return list(dict.fromkeys(value))


class DispatchInput(Input):
    workflow_id: int = Field(gt=0, strict=True)
    ref: str = Field(min_length=1, max_length=255)
    inputs: dict[str, str | bool | int] = Field(default_factory=dict, max_length=25)

    @field_validator('ref')
    @classmethod
    def valid_ref(cls, value):
        if any(ord(c) < 32 or ord(c) == 127 for c in value) or value.strip() != value:
            raise ValueError('Use a GitHub branch or tag reference')
        return value

    @field_validator('inputs')
    @classmethod
    def valid_inputs(cls, value):
        if len(dump(value)) > 16000 or any(not key or len(key) > 100 for key in value):
            raise ValueError('Workflow inputs are too large')
        return value


class GitHubStore:
    def __init__(self, db):
        self.db = db
        self.client = GitHubClient()

    def _record(self, connection_id):
        with self.db.transaction() as con:
            row = con.execute('SELECT * FROM github_connections WHERE id=?', (connection_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'GitHub connection not found. Reconnect the account.')
        return dict(row)

    @staticmethod
    def _public_connection(record):
        data = json.loads(record['data'])
        return {key: data[key] for key in CONNECTION_FIELDS if key in data}

    def get_connection(self, connection_id):
        return self._public_connection(self._record(connection_id))

    def status(self):
        with self.db.transaction() as con:
            records = con.execute('SELECT * FROM github_connections ORDER BY rowid').fetchall()
        slug = os.getenv('CI_GUARD_GITHUB_APP_SLUG', '')
        slug = slug if APP_SLUG.fullmatch(slug) else None
        try:
            _cipher()
            encryption = True
        except HTTPException:
            encryption = False
        return {'device_flow_configured': bool(os.getenv('CI_GUARD_GITHUB_CLIENT_ID') and slug and encryption),
                'environment_token_available': bool(os.getenv('CI_GUARD_GITHUB_TOKEN')),
                'encryption_configured': encryption, 'app_slug': slug,
                'install_url': f'https://github.com/apps/{slug}/installations/new' if slug else None,
                'connections': [self._public_connection(row) for row in records]}

    def credential(self, connection_id):
        record = self._record(connection_id)
        if record['expires_at'] is not None and record['expires_at'] <= time.time():
            raise HTTPException(401, 'GitHub authorization expired. Reconnect this account.')
        if record['source'] == 'environment':
            token = os.getenv('CI_GUARD_GITHUB_TOKEN', '')
            if not token:
                raise HTTPException(401, 'The explicit GitHub environment credential is unavailable. Reconnect this account.')
            return token
        return _decrypt(record['token_ciphertext'])

    def connect(self, token, *, source='token', expires_in=None):
        if source not in ('token', 'environment', 'device'):
            raise ValueError('Unsupported connection source')
        if not token or len(token) > 4096 or any(c.isspace() or ord(c) < 32 for c in token):
            raise HTTPException(422, 'Enter a valid GitHub access token.')
        ciphertext = _encrypt(token) if source != 'environment' else None
        identity = self.client.request(token, 'GET', '/user')
        if not isinstance(identity, dict) or not isinstance(identity.get('login'), str) or not APP_SLUG.fullmatch(identity['login']):
            raise HTTPException(502, 'GitHub returned an invalid account identity.')
        user_id = _positive_id(identity.get('id'))
        expires_at = None
        if expires_in is not None:
            if type(expires_in) is not int or expires_in <= 0:
                raise HTTPException(502, 'GitHub returned an invalid token expiration.')
            expires_at = time.time() + expires_in
        with self.db.transaction() as con:
            records = con.execute('SELECT * FROM github_connections WHERE source=?', (source,)).fetchall()
            previous = next((row for row in records if json.loads(row['data']).get('_user_id') == user_id), None)
            identifier = previous['id'] if previous else uuid.uuid4().hex
            data = {'id': identifier, 'login': identity['login'],
                    'avatar_url': f'https://avatars.githubusercontent.com/u/{user_id}',
                    'html_url': 'https://github.com/' + identity['login'], 'auth_type': source,
                    'created_at': json.loads(previous['data'])['created_at'] if previous else iso(),
                    '_user_id': user_id, '_app_slug': os.getenv('CI_GUARD_GITHUB_APP_SLUG', '')}
            if expires_at is not None:
                data['expires_at'] = iso(expires_at)
            con.execute('INSERT INTO github_connections(id,data,token_ciphertext,source,expires_at) VALUES (?,?,?,?,?) '
                        'ON CONFLICT(id) DO UPDATE SET data=excluded.data,token_ciphertext=excluded.token_ciphertext,expires_at=excluded.expires_at',
                        (identifier, dump(data), ciphertext, source, expires_at))
            con.execute("INSERT INTO configuration(key,data) VALUES ('github_auth_enforced','true') ON CONFLICT(key) DO UPDATE SET data='true'")
            self.db.audit(con, 'github_connected', f'GitHub account connected: {identity["login"]}', connection_id=identifier)
        return self.get_connection(identifier)

    def _verified_credential(self, connection_id):
        record = self._record(connection_id)
        token = self.credential(connection_id)
        identity = self.client.request(token, 'GET', '/user')
        if not isinstance(identity, dict) or identity.get('id') != json.loads(record['data']).get('_user_id'):
            raise HTTPException(401, 'GitHub credential identity changed. Reconnect this account explicitly.')
        return token, record

    def repositories(self, connection_id):
        token, record = self._verified_credential(connection_id)
        return self._repositories(token, record)

    def _repositories(self, token, record):
        if record['source'] == 'device':
            slug = json.loads(record['data']).get('_app_slug')
            if not slug:
                raise HTTPException(503, 'Configure the GitHub App slug and reconnect this account.')
            installations = self.client.paginate(token, '/user/installations', key='installations')
            rows = []
            for installation in installations:
                if not isinstance(installation, dict):
                    raise HTTPException(502, 'GitHub returned an invalid app installation.')
                if installation.get('app_slug') != slug:
                    continue
                installation_id = _positive_id(installation.get('id'))
                rows.extend(self.client.paginate(token, f'/user/installations/{installation_id}/repositories', key='repositories'))
                if len(rows) > 500:
                    raise HTTPException(502, 'Too many accessible GitHub repositories. Narrow the app installation selection.')
        else:
            rows = self.client.paginate(token, '/user/repos')
        repositories = {_repository(row)['id']: _repository(row) for row in rows}
        return sorted(repositories.values(), key=lambda row: row['full_name'].casefold())

    def disconnect(self, connection_id):
        self._record(connection_id)
        with self.db.transaction() as con:
            con.execute('DELETE FROM github_project_tokens WHERE project_id IN (SELECT id FROM github_projects WHERE connection_id=?)', (connection_id,))
            con.execute('DELETE FROM github_projects WHERE connection_id=?', (connection_id,))
            con.execute('DELETE FROM github_connections WHERE id=?', (connection_id,))
            self.db.audit(con, 'github_disconnected', 'GitHub connection removed; project credentials revoked.', connection_id=connection_id)

    def list_projects(self):
        with self.db.transaction() as con:
            return [json.loads(row['data']) for row in con.execute('SELECT data FROM github_projects ORDER BY rowid')]

    def get_project(self, project_id):
        with self.db.transaction() as con:
            row = con.execute('SELECT data FROM github_projects WHERE id=?', (project_id,)).fetchone()
        if row is None:
            raise HTTPException(404, 'Project not found.')
        return json.loads(row['data'])

    def create_project(self, connection_id, repository_id):
        repository = next((row for row in self.repositories(connection_id) if row['id'] == repository_id), None)
        if repository is None:
            raise HTTPException(403, 'This GitHub connection does not have access to the selected repository.')
        with self.db.transaction() as con:
            if con.execute('SELECT id FROM github_connections WHERE id=?', (connection_id,)).fetchone() is None:
                raise HTTPException(409, 'GitHub connection was disconnected. Reconnect before importing.')
            existing = con.execute('SELECT data FROM github_projects WHERE connection_id=? AND repository_id=?', (connection_id, repository_id)).fetchone()
            if existing:
                return json.loads(existing['data'])
            data = {**repository, 'id': uuid.uuid4().hex, 'repository_id': repository_id,
                    'connection_id': connection_id, 'created_at': iso()}
            con.execute('INSERT INTO github_projects(id,connection_id,repository_id,data) VALUES (?,?,?,?)',
                        (data['id'], connection_id, repository_id, dump(data)))
            self.db.audit(con, 'project_added', f'Project selected: {repository["full_name"]}', project_id=data['id'])
        return data

    def delete_project(self, project_id):
        self.get_project(project_id)
        with self.db.transaction() as con:
            con.execute('DELETE FROM github_project_tokens WHERE project_id=?', (project_id,))
            con.execute('DELETE FROM github_projects WHERE id=?', (project_id,))
            self.db.audit(con, 'project_removed', 'Project removed; runner token revoked.', project_id=project_id)

    def rotate_runner_token(self, project_id, dependency_ids):
        project = self.get_project(project_id)
        dependency_ids = list(dict.fromkeys(dependency_ids))
        if not dependency_ids or len(dependency_ids) > 100:
            raise HTTPException(422, 'Select one or more registered dependencies.')
        token = 'cg_proj_' + secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.db.transaction() as con:
            if con.execute('SELECT id FROM github_projects WHERE id=?', (project_id,)).fetchone() is None:
                raise HTTPException(404, 'Project not found.')
            if any(con.execute('SELECT id FROM dependencies WHERE id=?', (identifier,)).fetchone() is None for identifier in dependency_ids):
                raise HTTPException(422, 'Select registered dependencies only.')
            con.execute('INSERT INTO github_project_tokens(project_id,token_hash,dependency_ids) VALUES (?,?,?) '
                        'ON CONFLICT(project_id) DO UPDATE SET token_hash=excluded.token_hash,dependency_ids=excluded.dependency_ids',
                        (project_id, digest, dump(dependency_ids)))
            self.db.audit(con, 'project_token_rotated', 'Project runner token rotated.', project_id=project_id)
        return {'token': token, 'project_id': project_id, 'repository': project['full_name'], 'dependency_ids': dependency_ids}

    def authenticate_project_token(self, token):
        if not token or not token.startswith('cg_proj_') or len(token) > 256:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.db.transaction() as con:
            row = con.execute('SELECT t.project_id,t.dependency_ids,p.data FROM github_project_tokens t '
                              'JOIN github_projects p ON p.id=t.project_id JOIN github_connections c ON c.id=p.connection_id '
                              'WHERE t.token_hash=?', (digest,)).fetchone()
        if row is None:
            return None
        return {'project_id': row['project_id'], 'repository': json.loads(row['data'])['full_name'],
                'dependency_ids': json.loads(row['dependency_ids'])}

    def _project_access(self, project_id):
        project = self.get_project(project_id)
        token, record = self._verified_credential(project['connection_id'])
        if record['source'] == 'device' and not any(
                repo['id'] == project['repository_id'] for repo in self._repositories(token, record)):
            raise HTTPException(403, 'This repository is no longer authorized for the GitHub App. Check its installation access.')
        name = project['full_name']
        if not REPOSITORY_NAME.fullmatch(name) or any(part in ('.', '..') for part in name.split('/')):
            raise HTTPException(409, 'Stored project identity is invalid. Remove and reimport this project.')
        path = '/repos/' + name
        repository = _repository(self.client.request(token, 'GET', path))
        if repository['id'] != project['repository_id'] or repository['full_name'].casefold() != name.casefold():
            raise HTTPException(409, 'GitHub repository identity changed. Remove and reimport this project.')
        return token, path, project

    def workflows(self, project_id):
        token, path, project = self._project_access(project_id)
        rows = self.client.paginate(token, path + '/actions/workflows', key='workflows')
        result = []
        for row in rows:
            if not isinstance(row, dict):
                raise HTTPException(502, 'GitHub returned an invalid workflow.')
            result.append({'id': _positive_id(row.get('id')), 'name': str(row.get('name', ''))[:500],
                           'path': str(row.get('path', ''))[:1000], 'state': str(row.get('state', ''))[:100],
                           'html_url': _github_link(row.get('html_url'), project['html_url'], project['html_url'] + '/actions')})
        return result

    def runs(self, project_id):
        token, path, project = self._project_access(project_id)
        payload = self.client.request(token, 'GET', path + '/actions/runs', params={'per_page': 30, 'page': 1})
        if not isinstance(payload, dict) or not isinstance(payload.get('workflow_runs'), list):
            raise HTTPException(502, 'GitHub returned an invalid workflow run list.')
        fields = ('name', 'status', 'conclusion', 'head_branch', 'event', 'created_at', 'updated_at', 'run_number')
        result = []
        for row in payload['workflow_runs'][:30]:
            if not isinstance(row, dict):
                raise HTTPException(502, 'GitHub returned an invalid workflow run.')
            identifier = _positive_id(row.get('id'))
            result.append({**{key: row.get(key) for key in fields}, 'id': identifier,
                           'html_url': project['html_url'] + '/actions/runs/' + str(identifier)})
        return result

    def dispatch(self, project_id, body):
        token, path, project = self._project_access(project_id)
        workflow_path = path + '/actions/workflows/' + str(body.workflow_id)
        workflow = self.client.request(token, 'GET', workflow_path)
        if not isinstance(workflow, dict) or workflow.get('id') != body.workflow_id or workflow.get('state') != 'active':
            raise HTTPException(409, 'The selected workflow is unavailable or disabled.')
        self.client.request(token, 'POST', workflow_path + '/dispatches', json={'ref': body.ref, 'inputs': body.inputs})
        with self.db.transaction() as con:
            self.db.audit(con, 'github_workflow_dispatched', 'GitHub accepted a workflow dispatch.', project_id=project_id,
                          workflow_id=body.workflow_id)
        return {'status': 'accepted', 'html_url': project['html_url'] + '/actions'}

    def start_device(self):
        _cipher()
        client_id = os.getenv('CI_GUARD_GITHUB_CLIENT_ID', '')
        slug = os.getenv('CI_GUARD_GITHUB_APP_SLUG', '')
        if not client_id or not APP_SLUG.fullmatch(slug):
            raise HTTPException(503, 'Configure the registered GitHub App client ID and slug to enable device authorization.')
        result = self.client.oauth('/login/device/code', {'client_id': client_id})
        if not isinstance(result, dict) or result.get('error'):
            raise HTTPException(502, 'GitHub could not start device authorization. Check the app client ID and enable device flow.')
        if (not isinstance(result.get('device_code'), str) or not result['device_code'] or len(result['device_code']) > 4096
                or not isinstance(result.get('user_code'), str) or not re.fullmatch(r'[A-Z0-9-]{4,30}', result['user_code'])
                or result.get('verification_uri') != 'https://github.com/login/device'
                or type(result.get('expires_in')) is not int or not 1 <= result['expires_in'] <= 3600
                or type(result.get('interval')) is not int or not 1 <= result['interval'] <= 60):
            raise HTTPException(502, 'GitHub returned an invalid device authorization response.')
        identifier = uuid.uuid4().hex
        data = {'device_code': _encrypt(result['device_code']), 'expires_at': time.time() + result['expires_in'],
                'next_poll': time.time() + result['interval'], 'interval': result['interval'], 'state': 'pending',
                'client_id': client_id}
        with self.db.transaction() as con:
            # Bound stored outstanding handshakes and discard expired secrets.
            rows = con.execute('SELECT id,data FROM github_devices').fetchall()
            active = 0
            for row in rows:
                saved = json.loads(row['data'])
                if saved['expires_at'] <= time.time():
                    con.execute('DELETE FROM github_devices WHERE id=?', (row['id'],))
                else:
                    active += 1
            if active >= 20:
                raise HTTPException(429, 'Too many pending GitHub authorizations. Finish or wait for existing codes to expire.')
            con.execute('INSERT INTO github_devices(id,data) VALUES (?,?)', (identifier, dump(data)))
        return {'id': identifier, **{key: result[key] for key in ('user_code', 'verification_uri', 'expires_in', 'interval')}}

    def poll_device(self, identifier):
        with self.db.transaction() as con:
            row = con.execute('SELECT data FROM github_devices WHERE id=?', (identifier,)).fetchone()
            if row is None:
                raise HTTPException(404, 'Device authorization not found. Start again.')
            data = json.loads(row['data'])
            if data['state'] == 'connected':
                raise HTTPException(409, 'This authorization code has already been used.')
            if data['expires_at'] <= time.time():
                data.update(state='expired', device_code='')
                con.execute('UPDATE github_devices SET data=? WHERE id=?', (dump(data), identifier))
            if data['state'] in ('expired', 'denied'):
                return {'status': data['state']}
            if data['state'] == 'polling' or data['next_poll'] > time.time():
                return {'status': 'pending', 'interval': data['interval']}
            data.update(state='polling', next_poll=time.time() + data['interval'])
            con.execute('UPDATE github_devices SET data=? WHERE id=?', (dump(data), identifier))
        try:
            response = self.client.oauth('/login/oauth/access_token', {'client_id': data['client_id'],
                'device_code': _decrypt(data['device_code']), 'grant_type': 'urn:ietf:params:oauth:grant-type:device_code'})
            error = response.get('error') if isinstance(response, dict) else None
            if error:
                if error == 'authorization_pending':
                    data['state'] = 'pending'
                elif error == 'slow_down':
                    data.update(state='pending', interval=data['interval'] + 5)
                    data['next_poll'] = time.time() + data['interval']
                elif error in ('expired_token', 'access_denied'):
                    data.update(state='expired' if error == 'expired_token' else 'denied', device_code='')
                else:
                    data.update(state='denied', device_code='')
                    raise HTTPException(502, 'GitHub rejected device authorization. Check the app configuration and start again.')
                result = {'status': 'slow_down' if error == 'slow_down' else data['state'], 'interval': data['interval']}
            elif isinstance(response, dict) and isinstance(response.get('access_token'), str):
                connection = self.connect(response['access_token'], source='device', expires_in=response.get('expires_in'))
                data.update(state='connected', device_code='')
                result = {'status': 'connected', 'connection': connection}
            else:
                data.update(state='denied', device_code='')
                raise HTTPException(502, 'GitHub returned an invalid authorization response. Start again.')
        except Exception:
            if data['state'] == 'polling':
                data['state'] = 'pending'
            raise
        finally:
            with self.db.transaction() as con:
                con.execute('UPDATE github_devices SET data=? WHERE id=?', (dump(data), identifier))
        return result


def install_github_routes(app, db, strict_admin_dependency):
    store = GitHubStore(db)
    app.state.github_store = store
    router = APIRouter(dependencies=[Depends(strict_admin_dependency)])

    @router.get('/api/github/status')
    def status():
        return store.status()

    @router.post('/api/github/connect/token', status_code=201)
    def connect_token(body: TokenInput):
        return store.connect(body.token.get_secret_value())

    @router.post('/api/github/connect/environment', status_code=201)
    def connect_environment():
        token = os.getenv('CI_GUARD_GITHUB_TOKEN', '')
        if not token:
            raise HTTPException(503, 'CI_GUARD_GITHUB_TOKEN is not explicitly configured on this server.')
        return store.connect(token, source='environment')

    @router.post('/api/github/device/start', status_code=201)
    def start_device():
        return store.start_device()

    @router.post('/api/github/device/poll')
    def poll_device(body: DevicePollInput):
        return store.poll_device(body.id)

    @router.delete('/api/github/connections/{connection_id}', status_code=204)
    def disconnect(connection_id: str):
        store.disconnect(connection_id)
        return Response(status_code=204)

    @router.get('/api/github/connections/{connection_id}/repositories')
    def repositories(connection_id: str):
        return store.repositories(connection_id)

    @router.get('/api/projects')
    def projects():
        return store.list_projects()

    @router.post('/api/projects', status_code=201)
    def create_project(body: ProjectInput):
        return store.create_project(body.connection_id, body.repository_id)

    @router.delete('/api/projects/{project_id}', status_code=204)
    def delete_project(project_id: str):
        store.delete_project(project_id)
        return Response(status_code=204)

    @router.get('/api/projects/{project_id}/workflows')
    def workflows(project_id: str):
        return store.workflows(project_id)

    @router.get('/api/projects/{project_id}/actions/runs')
    def runs(project_id: str):
        return store.runs(project_id)

    @router.post('/api/projects/{project_id}/dispatch', status_code=202)
    def dispatch(project_id: str, body: DispatchInput):
        return store.dispatch(project_id, body)

    @router.post('/api/projects/{project_id}/runner-token', status_code=201)
    def runner_token(project_id: str, body: RunnerTokenInput):
        return store.rotate_runner_token(project_id, body.dependency_ids)

    app.include_router(router)
    return store
