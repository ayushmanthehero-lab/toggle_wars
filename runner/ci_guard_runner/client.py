"""Small authenticated client: unavailable coordination never grants retries."""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx


class CoordinationError(Exception):
    pass


class Coordinator:
    def __init__(self, server: str, token: str | None):
        parsed = urlsplit(server)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Server must be an HTTP(S) URL without credentials, query, or fragment.")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.client = httpx.Client(base_url=server.rstrip("/"), headers=headers, timeout=10,
                                   follow_redirects=False)

    def request(self, method: str, path: str, body: dict | None = None):
        try:
            response = self.client.request(method, path, json=body)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, (dict, list)):
                raise ValueError("expected JSON object or array")
            return payload
        except (httpx.HTTPError, ValueError) as exc:
            # No headers, raw response bodies, tokens, or request payloads in errors.
            status = f"HTTP {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
            raise CoordinationError(f"Coordinator request {method} {path} failed ({status}).") from exc

    def close(self):
        self.client.close()
