import copy
import json

import httpx
import pytest

from ci_guard.policy import Policy, PolicyProvider, PolicyWriteError


DEFAULTS = {
    'mode': 'observe',
    'max_job_retries': 2,
    'shared_dependency_retry_budget': 2,
    'dependency_cooldown_seconds': 30,
}


class MemoryDB:
    def __init__(self):
        self.value = DEFAULTS.copy()

    def get_demo_policy(self):
        return copy.deepcopy(self.value)

    def set_demo_policy(self, value):
        self.value = copy.deepcopy(value)


class Flags:
    def __init__(self, values):
        self.values = values

    def get_feature_value(self, name):
        return self.values[name]

    def is_feature_enabled(self, name):
        return True


class SDK:
    def __init__(self, values=None):
        self.values = values or {'ci_guard_' + k: v for k, v in DEFAULTS.items()}
        self.calls = []

    def get_identity_flags(self, identifier, traits):
        self.calls.append((identifier, traits))
        return Flags(self.values)

    def get_environment_flags(self):
        self.calls.append(('environment', {}))
        return Flags(self.values)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in ['FLAGSMITH_ENVIRONMENT_KEY', 'FLAGSMITH_API_URL', 'FLAGSMITH_ADMIN_API_KEY',
                'FLAGSMITH_ENVIRONMENT_ID', 'FLAGSMITH_ADMIN_API_URL', 'FLAGSMITH_ADMIN_AUTH_SCHEME']:
        monkeypatch.delenv(key, raising=False)


def configured_provider(monkeypatch, sdk=None, demo_mode=False):
    monkeypatch.setenv('FLAGSMITH_ENVIRONMENT_KEY', 'server-private-key')
    sdk = sdk or SDK()
    monkeypatch.setattr('ci_guard.policy.Flagsmith', lambda **kwargs: sdk)
    return PolicyProvider(MemoryDB(), demo_mode=demo_mode), sdk


def test_explicit_demo_policy_is_persistent_and_fingerprinted():
    db = MemoryDB()
    p = PolicyProvider(db, demo_mode=True)
    original = p.evaluate('owner/repo')
    assert original.source == 'demo'
    edited = p.update({**DEFAULTS, 'mode': 'enforce', 'shared_dependency_retry_budget': 0})
    assert edited.mode == 'enforce'
    assert edited.shared_dependency_retry_budget == 0
    assert edited.fingerprint != original.fingerprint
    assert PolicyProvider(db, demo_mode=True).evaluate('owner/repo') == edited
    assert original.mode == 'observe'


def test_unconfigured_live_policy_runs_once_with_explicit_error():
    policy = PolicyProvider(MemoryDB(), demo_mode=False).evaluate('owner/repo')
    assert policy.source == 'flagsmith'
    assert policy.mode == 'off'
    assert policy.max_job_retries == 0
    assert 'FLAGSMITH_ENVIRONMENT_KEY' in policy.error


def test_live_key_wins_over_demo_and_one_snapshot_is_evaluated(monkeypatch):
    provider, sdk = configured_provider(monkeypatch, demo_mode=True)
    policy = provider.evaluate('owner/repo')
    assert policy.source == 'flagsmith'
    assert policy.error is None
    assert sdk.calls == [('repo:owner/repo', {'repository': 'owner/repo', 'team': 'owner'})]


def test_live_changes_are_visible_without_recreating_provider(monkeypatch):
    provider, sdk = configured_provider(monkeypatch)
    first = provider.evaluate('owner/repo')
    sdk.values['ci_guard_mode'] = 'enforce'
    sdk.values['ci_guard_shared_dependency_retry_budget'] = '0'
    second = provider.evaluate('owner/repo')
    assert second.mode == 'enforce' and second.shared_dependency_retry_budget == 0
    assert first.mode == 'observe'
    assert first.fingerprint != second.fingerprint
    assert len(sdk.calls) == 2


@pytest.mark.parametrize('field,value', [('mode', 'oops'), ('max_job_retries', True),
    ('max_job_retries', '2.5'), ('shared_dependency_retry_budget', -1),
    ('dependency_cooldown_seconds', 3601)])
def test_invalid_live_values_never_fall_back_to_enforcement(monkeypatch, field, value):
    provider, sdk = configured_provider(monkeypatch)
    sdk.values['ci_guard_' + field] = value
    policy = provider.evaluate('owner/repo')
    assert policy.error and policy.mode == 'off' and policy.max_job_retries == 0


def test_missing_live_flag_fails_complete_snapshot(monkeypatch):
    provider, sdk = configured_provider(monkeypatch)
    del sdk.values['ci_guard_max_job_retries']
    policy = provider.evaluate('owner/repo')
    assert policy.error and policy.mode == 'off'


def test_sdk_failure_does_not_expose_secrets(monkeypatch):
    provider, sdk = configured_provider(monkeypatch)
    def fail(*args, **kwargs):
        raise RuntimeError('https://user:secret@host?token=private server-private-key')
    sdk.get_identity_flags = fail
    policy = provider.evaluate('owner/repo')
    assert policy.error
    assert all(secret not in policy.error for secret in ['secret', 'private', 'https://'])
    assert policy.max_job_retries == 0


def test_live_edits_require_admin_configuration(monkeypatch):
    provider, _ = configured_provider(monkeypatch)
    with pytest.raises(PolicyWriteError, match='FLAGSMITH_ADMIN_API_KEY'):
        provider.update(DEFAULTS)


def test_invalid_demo_update_does_not_mutate_saved_policy():
    db = MemoryDB()
    provider = PolicyProvider(db, demo_mode=True)
    with pytest.raises(ValueError):
        provider.update({**DEFAULTS, 'max_job_retries': -1})
    assert db.value == DEFAULTS


def test_live_update_writes_actual_environment_states(monkeypatch):
    sdk = SDK()
    monkeypatch.setenv('FLAGSMITH_ADMIN_API_KEY', 'admin-secret')
    monkeypatch.setenv('FLAGSMITH_ENVIRONMENT_ID', 'environment-api-key')
    monkeypatch.setenv('FLAGSMITH_API_URL', 'http://flagsmith:8000/api/v1/')
    provider, _ = configured_provider(monkeypatch, sdk)
    states = [{'id': i, 'feature': i, 'name': 'ci_guard_' + k,
               'feature_state_value': v, 'enabled': True}
              for i, (k, v) in enumerate(DEFAULTS.items(), start=1)]
    requests = []
    def handler(request):
        requests.append(request)
        assert request.headers['Authorization'] == 'Api-Key admin-secret'
        assert request.url.host == 'flagsmith'
        assert '/environments/environment-api-key/featurestates/' in request.url.path
        if request.method == 'GET':
            selected = [state for state in states if state['name'] == request.url.params.get('feature_name')]
            return httpx.Response(200, json={'count': len(selected), 'next': None, 'results': selected})
        state = states[int(request.url.path.strip('/').split('/')[-1]) - 1]
        body = json.loads(request.content)
        sdk.values[state['name']] = body['feature_state_value']
        state.update(body)
        return httpx.Response(200, json=state)
    real_client = httpx.Client
    monkeypatch.setattr('ci_guard.policy.httpx.Client', lambda **kwargs:
                        real_client(transport=httpx.MockTransport(handler), **kwargs))
    updated = provider.update({**DEFAULTS, 'mode': 'enforce', 'max_job_retries': 1})
    assert updated.source == 'flagsmith' and updated.mode == 'enforce'
    assert updated.max_job_retries == 1 and updated.error is None
    assert [r.method for r in requests].count('PATCH') == 4


def test_partial_live_write_failure_is_truthful(monkeypatch):
    monkeypatch.setenv('FLAGSMITH_ADMIN_API_KEY', 'admin-secret')
    monkeypatch.setenv('FLAGSMITH_ENVIRONMENT_ID', 'environment-api-key')
    provider, _ = configured_provider(monkeypatch)
    states = [{'id': i, 'feature': i, 'name': 'ci_guard_' + k,
               'feature_state_value': v, 'enabled': True}
              for i, (k, v) in enumerate(DEFAULTS.items(), start=1)]
    def handler(request):
        selected = [state for state in states if state['name'] == request.url.params.get('feature_name')]
        return httpx.Response(200, json={'count': len(selected), 'next': None, 'results': selected}) if request.method == 'GET' else httpx.Response(500)
    real_client = httpx.Client
    monkeypatch.setattr('ci_guard.policy.httpx.Client', lambda **kwargs:
                        real_client(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(PolicyWriteError, match='may have changed') as error:
        provider.update(DEFAULTS)
    assert 'admin-secret' not in str(error.value)
