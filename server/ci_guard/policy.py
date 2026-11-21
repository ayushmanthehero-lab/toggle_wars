"""Flagsmith policy snapshots and explicitly configured persistent demo policies.

SDK reads use one complete flag response per decision. Management writes target
Flagsmith's environment feature-state API; FLAGSMITH_ENVIRONMENT_ID is the
*environment API key* used in that API's path, not the numeric database ID.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from typing import Any, Literal, Protocol
from urllib.parse import quote, urlsplit

import httpx
from flagsmith import Flagsmith
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from urllib3.util.retry import Retry


POLICY_FIELDS = (
    'mode', 'max_job_retries', 'shared_dependency_retry_budget',
    'dependency_cooldown_seconds',
)
FLAG_NAMES = {field: 'ci_guard_' + field for field in POLICY_FIELDS}


class Policy(BaseModel):
    """An immutable, validated policy suitable for preserving in decision history."""

    model_config = ConfigDict(frozen=True, extra='forbid')
    mode: Literal['off', 'observe', 'enforce'] = 'observe'
    max_job_retries: int = Field(default=2, ge=0, le=10, strict=True)
    shared_dependency_retry_budget: int = Field(default=2, ge=0, le=100, strict=True)
    dependency_cooldown_seconds: int = Field(default=30, ge=0, le=3600, strict=True)
    source: str = 'demo'
    fingerprint: str = ''
    error: str | None = None

    @model_validator(mode='after')
    def add_fingerprint(self) -> 'Policy':
        payload = self.model_dump(exclude={'fingerprint'})
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        object.__setattr__(self, 'fingerprint', digest)
        return self


class PolicyStore(Protocol):
    def get_demo_policy(self) -> dict[str, Any]: ...
    def set_demo_policy(self, values: dict[str, Any]) -> None: ...


class PolicyWriteError(ValueError):
    """A policy save was unavailable or could not be confirmed."""


class PolicyProvider:
    def __init__(self, db: PolicyStore, demo_mode: bool = False):
        self.db = db
        self.environment_key = os.getenv('FLAGSMITH_ENVIRONMENT_KEY', '').strip()
        self.api_url = os.getenv('FLAGSMITH_API_URL', 'https://edge.api.flagsmith.com/api/v1/').rstrip('/') + '/'
        self.admin_api_url = os.getenv('FLAGSMITH_ADMIN_API_URL', self.api_url).rstrip('/') + '/'
        self.admin_key = os.getenv('FLAGSMITH_ADMIN_API_KEY', '').strip()
        self.environment_id = os.getenv('FLAGSMITH_ENVIRONMENT_ID', '').strip()
        self.admin_auth_scheme = os.getenv('FLAGSMITH_ADMIN_AUTH_SCHEME', 'Api-Key')
        # Demo tools may coexist with a real Flagsmith deployment. An explicit
        # live key always takes precedence over the local demo policy store.
        self.demo_mode = bool(demo_mode and not self.environment_key)
        self.source = 'demo' if self.demo_mode else 'flagsmith'
        self._client: Flagsmith | None = None
        self._client_lock = threading.Lock()
        self._write_lock = threading.Lock()

    @property
    def editable(self) -> bool:
        return self.demo_mode or bool(self.environment_key and self.admin_key and self.environment_id)

    @staticmethod
    def _fields(policy: Policy | dict[str, Any]) -> dict[str, Any]:
        data = policy.model_dump() if isinstance(policy, Policy) else policy
        return {key: data[key] for key in POLICY_FIELDS if key in data}

    def _failure(self, message: str) -> Policy:
        return Policy(mode='off', max_job_retries=0, shared_dependency_retry_budget=0,
                      dependency_cooldown_seconds=30, source='flagsmith', error=message)

    def _sdk(self) -> Flagsmith:
        with self._client_lock:
            if self._client is None:
                self._client = Flagsmith(
                    environment_key=self.environment_key,
                    api_url=self.api_url,
                    request_timeout_seconds=3,
                    retries=Retry(total=0, connect=0, read=0, redirect=0),
                    enable_local_evaluation=False,
                    enable_analytics=False,
                )
        return self._client

    def evaluate(self, repository: str = '') -> Policy:
        if self.demo_mode:
            try:
                return Policy(**self._fields(self.db.get_demo_policy() or {}), source='demo')
            except (ValidationError, TypeError, ValueError):
                # Corrupted local policy is also fail-open for one real command,
                # never an invented working policy or an enforcement decision.
                return Policy(mode='off', max_job_retries=0, shared_dependency_retry_budget=0,
                              source='demo', error='Stored demo policy is invalid; save a valid policy to restore retries.')
        if not self.environment_key:
            return self._failure('FLAGSMITH_ENVIRONMENT_KEY is not configured; run once without retries.')
        try:
            client = self._sdk()
            flags = (client.get_identity_flags(
                identifier='repo:' + repository,
                traits={'repository': repository, 'team': repository.split('/', 1)[0]},
            ) if repository else client.get_environment_flags())
            values: dict[str, Any] = {}
            for field, flag_name in FLAG_NAMES.items():
                value = flags.get_feature_value(flag_name)
                if field != 'mode' and isinstance(value, str) and re.fullmatch(r'[0-9]+', value):
                    value = int(value)
                values[field] = value
            # The mode flag's enabled switch is an emergency disable. Numeric
            # flags still must parse, so an incomplete response is never trusted.
            if not flags.is_feature_enabled(FLAG_NAMES['mode']):
                values['mode'] = 'off'
            return Policy(**values, source='flagsmith')
        except (ValidationError, KeyError, TypeError, ValueError):
            return self._failure('Flagsmith policy is incomplete or invalid; check all four ci_guard_ flags. Run once without retries.')
        except Exception as exc:
            # SDK exception messages can contain URLs, headers, or response
            # bodies. Never return those messages in reports or to browsers.
            return self._failure(f'Flagsmith evaluation failed ({type(exc).__name__}); run once without retries.')

    def update(self, values: dict[str, Any]) -> Policy:
        with self._write_lock:
            supplied = self._fields(values)
            if self.demo_mode:
                current = self._fields(self.db.get_demo_policy() or {})
                policy = Policy(**{**current, **supplied}, source='demo')
                self.db.set_demo_policy(self._fields(policy))
                return policy
            if not self.editable:
                raise PolicyWriteError(
                    'Live policy is read-only. Configure FLAGSMITH_ADMIN_API_KEY and '
                    'FLAGSMITH_ENVIRONMENT_ID (the environment API key), or edit the flags in Flagsmith.'
                )
            if len(supplied) != len(POLICY_FIELDS):
                current = self.evaluate()
                if current.error:
                    raise PolicyWriteError('Cannot merge a partial policy while Flagsmith evaluation is unavailable; supply all four fields.')
                supplied = {**self._fields(current), **supplied}
            policy = Policy(**supplied, source='flagsmith')
            if self.admin_auth_scheme not in ('Api-Key', 'Token'):
                raise PolicyWriteError('FLAGSMITH_ADMIN_AUTH_SCHEME must be Api-Key or Token.')
            parsed = urlsplit(self.admin_api_url)
            if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
                raise PolicyWriteError('FLAGSMITH_ADMIN_API_URL must be an HTTP(S) URL without credentials.')
            endpoint = self.admin_api_url + 'environments/' + quote(self.environment_id, safe='') + '/featurestates/'
            attempted_write = False
            try:
                with httpx.Client(timeout=5.0, follow_redirects=False, trust_env=False,
                                  headers={'Authorization': f'{self.admin_auth_scheme} {self.admin_key}'}) as client:
                    by_name: dict[str, dict[str, Any]] = {}
                    # Management responses expose `feature` as a numeric ID on
                    # supported self-hosted releases. Filter by the documented
                    # feature_name query rather than assuming SDK-style nesting.
                    # Every lookup remains on this fixed administrator origin.
                    for name in FLAG_NAMES.values():
                        response = client.get(endpoint, params={'feature_name': name, 'page_size': 100})
                        response.raise_for_status()
                        payload = response.json()
                        matches = payload if isinstance(payload, list) else payload['results']
                        if len(matches) != 1 or (isinstance(payload, dict) and payload.get('next')):
                            raise PolicyWriteError('Create exactly one base state for each of the four ci_guard_ feature flags in this Flagsmith environment before saving.')
                        by_name[name] = matches[0]
                    for field, name in FLAG_NAMES.items():
                        state_id = int(by_name[name]['id'])
                        attempted_write = True
                        response = client.patch(endpoint + str(state_id) + '/', json={
                            'feature_state_value': getattr(policy, field), 'enabled': True,
                        })
                        response.raise_for_status()
                # Confirm the saved state using an actual complete SDK response.
                saved = self.evaluate()
                if saved.error or self._fields(saved) != self._fields(policy):
                    raise PolicyWriteError('Flagsmith save could not be confirmed. Some values may have changed; reload the policy before retrying.')
                return saved
            except PolicyWriteError:
                raise
            except Exception as exc:
                suffix = (' Some values may have changed; reload the policy before retrying.'
                          if attempted_write else ' No policy writes were attempted.')
                raise PolicyWriteError(f'Flagsmith policy save failed ({type(exc).__name__}).{suffix}') from None
