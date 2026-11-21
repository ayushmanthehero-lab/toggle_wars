"""Exercise the real HTTP adapter against GitHub-shaped transport responses."""
import importlib
import json
import socket
import socketserver
import threading

from fastapi import HTTPException
import httpx
import pytest


def client(handler):
    module = importlib.import_module('ci_guard.github_client')
    return module.GitHubClient(transport=httpx.MockTransport(handler))


def test_api_request_sends_credentials_only_to_fixed_api_origin():
    received = []

    def respond(request):
        received.append(request)
        return httpx.Response(200, json={'login': 'octocat'})

    result = client(respond).request('private-token', 'GET', '/user', params={'page': 1})
    assert result == {'login': 'octocat'}
    assert str(received[0].url) == 'https://api.github.com/user?page=1'
    assert received[0].headers['Authorization'] == 'Bearer private-token'
    assert received[0].headers['Accept'] == 'application/vnd.github+json'


@pytest.mark.parametrize('path', [
    'https://evil.example/user', '//evil.example/user', '/repos/../user',
    '/repos/%2e%2e/user', '/repos/a\\b/user', '/user?redirect=evil', '/user#fragment',
])
def test_external_or_ambiguous_api_paths_are_rejected_before_sending(path):
    received = []
    adapter = client(lambda request: received.append(request) or httpx.Response(200, json={}))
    with pytest.raises(HTTPException) as caught:
        adapter.request('private-token', 'GET', path)
    assert caught.value.status_code == 422
    assert received == []


def test_redirect_never_forwards_credentials_to_another_origin():
    received = []

    def respond(request):
        received.append(request)
        return httpx.Response(302, headers={'Location': 'https://evil.example/steal'})

    with pytest.raises(HTTPException) as caught:
        client(respond).request('private-token', 'GET', '/user')
    assert caught.value.status_code == 502
    assert len(received) == 1
    assert 'evil.example' not in str(caught.value.detail)


@pytest.mark.parametrize('status', [401, 403, 404, 429, 500])
def test_remote_errors_never_copy_response_secrets(status):
    def respond(request):
        return httpx.Response(status, json={'message': 'private-token https://evil.example/secret'})

    with pytest.raises(HTTPException) as caught:
        client(respond).request('private-token', 'GET', '/user')
    detail = str(caught.value.detail).lower()
    assert 'private-token' not in detail and 'evil.example' not in detail
    assert str(status) in detail
    if status == 401:
        assert 'reconnect' in detail
    if status == 403:
        assert 'permission' in detail


def test_dispatch_transport_failure_is_ambiguous_and_never_retried():
    received = []

    def respond(request):
        received.append(request)
        raise httpx.ReadTimeout('private-token leaked by upstream exception', request=request)

    with pytest.raises(HTTPException) as caught:
        client(respond).request('private-token', 'POST', '/repos/acme/service/actions/workflows/3/dispatches', json={'ref': 'main'})
    assert caught.value.status_code == 502
    assert len(received) == 1
    assert 'private-token' not in str(caught.value.detail)
    assert 'may have been accepted' in str(caught.value.detail).lower()
    assert 'retry' in str(caught.value.detail).lower()


def test_dispatch_204_is_success_without_inventing_a_run():
    received = []

    def respond(request):
        received.append(request)
        return httpx.Response(204)

    assert client(respond).request('token', 'POST', '/repos/acme/service/actions/workflows/3/dispatches', json={'ref': 'main'}) is None
    assert json.loads(received[0].content) == {'ref': 'main'}


def test_invalid_json_returns_safe_remote_failure():
    with pytest.raises(HTTPException) as caught:
        client(lambda request: httpx.Response(200, text='private-token')).request('token', 'GET', '/user')
    assert caught.value.status_code == 502
    assert 'private-token' not in str(caught.value.detail)


def test_pagination_reads_fixed_paths_and_ignores_remote_link_origin():
    received = []

    def respond(request):
        received.append(request)
        page = int(request.url.params['page'])
        entries = [{'id': index} for index in range(100)] if page == 1 else [{'id': 100}]
        return httpx.Response(200, json={'repositories': entries}, headers={'Link': '<https://evil.example/secret>; rel="next"'})

    result = client(respond).paginate('private-token', '/user/installations/12/repositories', key='repositories')
    assert len(result) == 101 and result[-1] == {'id': 100}
    assert [request.url.host for request in received] == ['api.github.com', 'api.github.com']
    assert [request.url.params['page'] for request in received] == ['1', '2']
    assert all(request.url.params['per_page'] == '100' for request in received)


def test_full_pagination_limit_fails_explicitly_instead_of_truncating_access():
    received = []

    def respond(request):
        received.append(request)
        return httpx.Response(200, json=[{'id': index} for index in range(100)])

    with pytest.raises(HTTPException) as caught:
        client(respond).paginate('private-token', '/user/repos')
    assert len(received) == 5
    assert caught.value.status_code == 502
    assert 'limit' in str(caught.value.detail).lower()


def test_invalid_pagination_envelope_does_not_become_empty_repository_list():
    with pytest.raises(HTTPException) as caught:
        client(lambda request: httpx.Response(200, json={'unexpected': []})).paginate('token', '/user/installations', key='installations')
    assert caught.value.status_code == 502


@pytest.mark.parametrize('endpoint', ['/login/device/code', '/login/oauth/access_token'])
def test_oauth_uses_github_form_endpoint_and_preserves_200_oauth_errors(endpoint):
    received = []

    def respond(request):
        received.append(request)
        return httpx.Response(200, json={'error': 'authorization_pending', 'error_description': 'Device not approved'})

    result = client(respond).oauth(endpoint, {'client_id': 'public-id', 'device_code': 'private-code'})
    assert result['error'] == 'authorization_pending'
    assert str(received[0].url) == 'https://github.com' + endpoint
    assert received[0].headers['Accept'] == 'application/json'
    assert received[0].headers['Content-Type'] == 'application/x-www-form-urlencoded'
    assert 'Authorization' not in received[0].headers
    assert b'device_code=private-code' in received[0].content


def test_oauth_rejects_other_endpoints_without_sending_device_secret():
    received = []
    with pytest.raises(HTTPException) as caught:
        client(lambda request: received.append(request) or httpx.Response(200, json={})).oauth('https://evil.example/token', {'device_code': 'secret'})
    assert caught.value.status_code == 422
    assert received == []


@pytest.mark.parametrize('oauth,origin', [(False, 'api.github.com'), (True, 'github.com')])
def test_operator_https_proxy_receives_fixed_origin_tunnel_without_account_secrets(monkeypatch, oauth, origin):
    """Disabling environment proxies must fail without making a real external call."""
    received = []

    class Proxy(socketserver.StreamRequestHandler):
        def handle(self):
            headers = []
            while line := self.rfile.readline():
                headers.append(line)
                if line == b'\r\n':
                    break
            received.append(b''.join(headers))
            self.wfile.write(b'HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n')

    with socketserver.TCPServer(('127.0.0.1', 0), Proxy) as proxy:
        proxy.timeout = 0.5
        address = proxy.server_address
        connect = socket.create_connection

        def only_local_proxy(target, *args, **kwargs):
            if target != address:
                raise OSError('External network access is disabled in this test')
            return connect(target, *args, **kwargs)

        monkeypatch.setattr(socket, 'create_connection', only_local_proxy)
        for name in ('HTTP_PROXY', 'ALL_PROXY', 'NO_PROXY', 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv('HTTPS_PROXY', f'http://{address[0]}:{address[1]}')
        thread = threading.Thread(target=proxy.handle_request, daemon=True)
        thread.start()
        adapter = importlib.import_module('ci_guard.github_client').GitHubClient()
        with pytest.raises(HTTPException) as caught:
            if oauth:
                adapter.oauth('/login/device/code', {'client_id': 'public-id', 'device_code': 'private-code'})
            else:
                adapter.request('private-token', 'GET', '/user')
        thread.join(timeout=2)

    assert caught.value.status_code == 502
    assert len(received) == 1
    assert received[0].startswith(f'CONNECT {origin}:443 HTTP/1.1\r\n'.encode())
    assert b'private-token' not in received[0] and b'private-code' not in received[0]
