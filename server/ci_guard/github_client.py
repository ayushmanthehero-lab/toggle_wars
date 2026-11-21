"""A bounded GitHub HTTP boundary; credentials never follow caller supplied URLs."""
from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException
import httpx


class GitHubClient:
    """Use fixed public GitHub origins and never retry a remote mutation."""

    def __init__(self, transport: httpx.BaseTransport | None = None):
        self.transport = transport

    @staticmethod
    def _api_path(path: str) -> str:
        if (not isinstance(path, str) or not re.fullmatch(r'/[A-Za-z0-9_./-]+', path)
                or path.startswith('//') or '//' in path
                or any(part in ('.', '..') for part in path.split('/'))):
            raise HTTPException(422, 'GitHub API path must be a validated relative resource path.')
        return path

    @staticmethod
    def _response(response: httpx.Response) -> Any:
        status = response.status_code
        if not 200 <= status < 300:
            if status == 401:
                message = 'GitHub rejected the account credential (HTTP 401). Reconnect the account.'
            elif status == 403:
                message = 'GitHub denied this operation (HTTP 403). Check app installation, repository permissions, and rate limits.'
            elif status == 404:
                message = 'GitHub could not find or permit access to this resource (HTTP 404). Check repository access.'
            elif status == 429:
                message = 'GitHub rate limit reached (HTTP 429). Wait before another request.'
            elif 300 <= status < 400:
                message = f'GitHub returned an unexpected redirect (HTTP {status}); redirects are not followed.'
            else:
                message = f'GitHub request failed (HTTP {status}). Check GitHub availability and the requested operation.'
            # Do not copy remote bodies, headers, exception text, or URLs here.
            raise HTTPException(502, message)
        if status == 204:
            return None
        try:
            return response.json()
        except (ValueError, UnicodeError):
            raise HTTPException(502, 'GitHub returned an invalid JSON response.') from None

    def _send(self, method: str, url: str, *, headers: dict, params=None,
              json=None, data=None, dispatch: bool = False):
        try:
            # Honor deployment-managed proxies and CA bundles; destinations and
            # redirect behavior remain fixed, and TLS verification stays enabled.
            with httpx.Client(timeout=8.0, follow_redirects=False, trust_env=True,
                              transport=self.transport) as client:
                response = client.request(method, url, headers=headers, params=params,
                                          json=json, data=data)
        except httpx.RequestError:
            message = ('GitHub workflow dispatch may have been accepted, but confirmation was lost. '
                       'Check repository Actions before any manual retry; CI Guard did not retry the dispatch.'
                       if dispatch else 'GitHub could not be reached. Check connectivity and try again.')
            raise HTTPException(502, message) from None
        return self._response(response)

    def request(self, token: str, method: str, path: str, *, params=None, json=None):
        path = self._api_path(path)
        if (not isinstance(token, str) or not token or len(token) > 4096
                or any(ord(char) < 33 or ord(char) > 126 for char in token)):
            raise HTTPException(422, 'A valid GitHub credential is required.')
        if method not in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE'):
            raise HTTPException(422, 'Unsupported GitHub request method.')
        return self._send(method, 'https://api.github.com' + path, params=params, json=json,
                          headers={'Authorization': 'Bearer ' + token,
                                   'Accept': 'application/vnd.github+json',
                                   'X-GitHub-Api-Version': '2022-11-28'},
                          dispatch=method == 'POST' and path.endswith('/dispatches'))

    def paginate(self, token: str, path: str, key: str | None = None) -> list:
        """Never follow an untrusted Link URL or silently claim a partial access list."""
        result = []
        for page in range(1, 6):
            payload = self.request(token, 'GET', path, params={'page': page, 'per_page': 100})
            entries = payload.get(key) if key and isinstance(payload, dict) else payload if key is None else None
            if not isinstance(entries, list) or len(entries) > 100:
                raise HTTPException(502, 'GitHub returned an invalid paginated response.')
            result.extend(entries)
            if len(entries) < 100:
                return result
        raise HTTPException(502, 'GitHub pagination limit reached. Narrow repository access before listing again.')

    def oauth(self, endpoint: str, data: dict) -> dict:
        if endpoint not in ('/login/device/code', '/login/oauth/access_token'):
            raise HTTPException(422, 'Unsupported GitHub authorization endpoint.')
        payload = self._send('POST', 'https://github.com' + endpoint,
                             headers={'Accept': 'application/json'}, data=data)
        if not isinstance(payload, dict):
            raise HTTPException(502, 'GitHub returned an invalid authorization response.')
        # GitHub reports pending/slow_down/expired_token as HTTP 200 OAuth errors.
        # The service owns their state transitions and must not treat them as tokens.
        return payload
