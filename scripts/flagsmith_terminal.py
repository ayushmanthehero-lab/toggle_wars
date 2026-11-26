"""CI Guard's terminal dashboard for real, project-scoped Flagsmith defaults."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import shlex
import sys
from urllib.parse import quote, urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
from ci_guard.policy import FLAG_NAMES, Policy

POLICY_FLAGS = {name: attr for attr, name in FLAG_NAMES.items()}


class TerminalError(ValueError):
    """A user-facing error with no request URL, response body or credentials."""


@dataclass(frozen=True)
class Environment:
    id: int
    name: str
    key: str = field(repr=False)
    locked: bool = False


@dataclass(frozen=True)
class Flag:
    id: int
    name: str
    protected: bool


@dataclass(frozen=True)
class State:
    id: int
    enabled: bool
    value: str | int | bool | None = field(repr=False)
    protected: bool = False

    def display(self, name):
        switch = "ON" if self.enabled else "OFF"
        if name in POLICY_FLAGS and type(self.value) in (str, int):
            try:
                policy = Policy(**{POLICY_FLAGS[name]: _policy_value(name, str(self.value))})
                return f"{getattr(policy, POLICY_FLAGS[name])} ({switch})"
            except ValueError:
                return f"invalid policy ({switch})"
        return switch


@dataclass(frozen=True)
class Change:
    env: Environment
    name: str
    state: State
    key: str
    value: bool | str | int

    @property
    def before(self):
        return self.state.enabled if self.key == "enabled" else self.state.value

    @property
    def token(self):
        return f"apply {self.env.id}"


def _policy_value(name, raw):
    attr = POLICY_FLAGS[name]
    try:
        value = raw.casefold() if attr == "mode" else int(raw)
        return getattr(Policy(**{attr: value}), attr)
    except (ValueError, TypeError):
        limit = Policy.model_fields[attr].metadata
        guidance = "off, observe, or enforce" if attr == "mode" else f"an integer from {limit[0].ge} to {limit[1].le}"
        raise TerminalError(f"{name} requires {guidance}.") from None


class FlagsmithTerminal:
    def __init__(self, url, token, project, writable, *, scheme="Api-Key", transport=None):
        try:
            parsed = urlsplit(url)
            if (parsed.scheme not in ("https", "http") or not parsed.hostname or parsed.username
                    or parsed.password or parsed.query or parsed.fragment or parsed.path.rstrip("/") != "/api/v1"
                    or (parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"))
                    or not project > 0 or not token or not token.isascii()
                    or any(c.isspace() for c in token) or scheme not in ("Api-Key", "Token")):
                raise ValueError
            _ = parsed.port
        except ValueError:
            raise TerminalError("Set a valid Flagsmith HTTPS Management API URL (HTTP only on loopback), project ID and token.") from None
        self.project = project
        self.writable = frozenset(writable)
        self._client = httpx.Client(base_url=url.rstrip("/") + "/", headers={"Authorization": f"{scheme} {token}"},
                                    timeout=5, follow_redirects=False, trust_env=False, transport=transport)

    def close(self):
        self._client.close()

    def _request(self, method, route, **kw):
        try:
            response = self._client.request(method, route, **kw)
            if not response.is_success:
                raise TerminalError(f"Flagsmith {method} returned HTTP {response.status_code}.")
            return response.json() if response.content else None
        except TerminalError:
            raise
        except (httpx.HTTPError, ValueError):
            raise TerminalError(f"Flagsmith {method} did not return a usable response.") from None

    def _list(self, route, **filters):
        result = []
        for page in range(1, 51):
            data = self._request("GET", route, params={**filters, "page": page, "page_size": 100})
            if isinstance(data, list) and page == 1:
                return data
            if not isinstance(data, dict) or not isinstance(data.get("results"), list) or "next" not in data:
                break
            result.extend(data["results"])
            if not data["next"]:
                return result
        raise TerminalError("Flagsmith returned an invalid or oversized list.")

    def _environments(self):
        envs = {}
        for row in self._list("environments/", project=self.project):
            try:
                if int(row["project"]) != self.project:
                    raise ValueError
                eid, name, key = int(row["id"]), row["name"], row["api_key"]
                if eid < 1 or eid in envs or not isinstance(name, str) or not name or not isinstance(key, str) or not key:
                    raise ValueError
                # Always lock known production names, as well as any environment
                # that requires approval/versioning. Other names are opt-in by ID.
                prod = re.search(r"\b(prod|production)\b", name.casefold().replace("_", " "))
                locked = (eid not in self.writable or bool(prod)
                          or row.get("use_v2_feature_versioning") is not False
                          or row.get("minimum_change_request_approvals", -1) not in (0, None))
                envs[eid] = Environment(eid, name, key, locked)
            except (TypeError, KeyError, ValueError):
                raise TerminalError("Could not safely identify the project's environments.") from None
        if len({e.key for e in envs.values()}) != len(envs):
            raise TerminalError("Flagsmith returned duplicate environment credentials.")
        return envs

    def _flags(self):
        all_tags = self._list(f"projects/{self.project}/tags/")
        try:
            tags = {int(t["id"]): t for t in all_tags}
            if len(tags) != len(all_tags):
                raise ValueError
            catalog = {}
            for raw in self._list(f"projects/{self.project}/features/"):
                fid, name = int(raw["id"]), raw["name"]
                if ("project" in raw and raw["project"] != self.project) or fid < 1 or fid in catalog or not isinstance(name, str) or not name:
                    raise ValueError
                raw_tags = raw.get("tags")
                protected = (
                    raw.get("is_archived") is not False or raw.get("type") != "STANDARD"
                    or bool(raw.get("multivariate_options")) or raw.get("is_server_key_only") is True
                    or not isinstance(raw_tags, list)
                    or any(tag not in tags or str(tags[tag].get("label", "")).strip().casefold() == "protected"
                           for tag in raw_tags)
                )
                catalog[fid] = Flag(fid, name, protected)
            if len({f.name for f in catalog.values()}) != len(catalog):
                raise ValueError
            return catalog
        except (KeyError, TypeError, ValueError):
            raise TerminalError("Could not safely identify the project's flag metadata.") from None

    @staticmethod
    def _route(env):
        return f"environments/{quote(env.key, safe='')}/featurestates/"

    def _states(self, env, flags, **filters):
        states = {}
        try:
            for raw in self._list(self._route(env), **filters):
                fid, sid = int(raw["feature"]), int(raw["id"])
                if (raw.get("identity") is not None or raw.get("feature_segment") is not None
                        or raw.get("environment") != env.id or sid < 1 or type(raw.get("enabled")) is not bool):
                    raise ValueError
                if fid not in flags:
                    continue
                name = flags[fid].name
                if name in states:
                    raise ValueError
                states[name] = State(sid, raw["enabled"], raw.get("feature_state_value"),
                                     flags[fid].protected or bool(raw.get("multivariate_feature_state_values")))
            return states
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, TerminalError):
                raise
            raise TerminalError("Flagsmith returned ambiguous or invalid base flag states.") from None

    def snapshot(self):
        envs = sorted(self._environments().values(), key=lambda env: (env.name.casefold(), env.id))
        flags = self._flags()
        return envs, {env.id: self._states(env, flags) for env in envs}

    def _write_target(self, eid, name):
        env = self._environments().get(eid)
        if env is None:
            raise TerminalError("That environment ID isn't in this Flagsmith project.")
        if env.locked:
            raise TerminalError("That environment is read-only.")
        flags = self._flags()
        flag = next((flag for flag in flags.values() if flag.name == name), None)
        state = self._states(env, flags, feature=flag.id).get(name) if flag is not None else None
        if state is None:
            raise TerminalError("Flag has no unique base state in that environment.")
        if state.protected:
            raise TerminalError("Protected, archived and multivariate flags are read-only.")
        return env, state

    def preview(self, env_id, name, raw):
        env, state = self._write_target(env_id, name)
        if name in POLICY_FLAGS:
            value = _policy_value(name, raw)
            if name == FLAG_NAMES["mode"] and not state.enabled and value != "off":
                raise TerminalError("The mode flag is disabled. Enable it in Flagsmith before changing its mode.")
            # Refuse to preview an unexpected value instead of displaying its raw contents.
            _policy_value(name, str(state.value))
            return Change(env, name, state, "feature_state_value", value)
        if raw.casefold() not in ("on", "off"):
            raise TerminalError("Use on or off for an ordinary flag.")
        return Change(env, name, state, "enabled", raw.casefold() == "on")

    def apply(self, change):
        env, state = self._write_target(change.env.id, change.name)
        if env != change.env or state != change.state:
            raise TerminalError("The environment or flag changed since preview. Review it again.")
        try:
            route = self._route(env) + f"{state.id}/"
            if change.before != change.value:
                self._request("PATCH", route, json={change.key: change.value})
            after = self._request("GET", route)
            if (not isinstance(after, dict) or after.get("id") != state.id or after.get("environment") != env.id
                    or after.get("identity") is not None or after.get("feature_segment") is not None
                    or type(after.get("enabled")) is not bool or after.get(change.key) != change.value):
                raise TerminalError("Flagsmith returned a different state.")
            return State(state.id, after["enabled"], after.get("feature_state_value"))
        except TerminalError as exc:
            raise TerminalError(f"Write not verified; refresh before retrying. {exc}") from None


def safe(text):
    return "".join(c if c.isprintable() else "?" for c in str(text))


def label(value):
    return ("ON" if value else "OFF") if isinstance(value, bool) else safe(value)


def confirm_and_apply(client, change, *, confirm=input):
    question = (f"{safe(change.env.name)} [#{change.env.id}] · {safe(change.name)}: "
                f"{label(change.before)} → {label(change.value)}. Type '{change.token}' to confirm: ")
    return client.apply(change) if confirm(question).strip() == change.token else None


def show(client, term=""):
    environments, matrix = client.snapshot()
    if not environments:
        print("No accessible environments in this Flagsmith project.")
        return
    names = sorted({name for states in matrix.values() for name in states if term.casefold() in name.casefold()},
                   key=lambda name: (name not in POLICY_FLAGS, name.casefold()))
    headers = ["Flag (environment default)", *[f"{safe(e.name)} [#{e.id}]" + (" RO" if e.locked else "") for e in environments]]
    def cell(e, name):
        state = matrix[e.id].get(name)
        if state is None:
            return "—"
        return state.display(name) + (" RO" if state.protected and not e.locked else "")
    rows = [headers, *[[safe(name), *[cell(e, name) for e in environments]] for name in names]]
    widths = [max(map(len, col)) for col in zip(*rows)]
    print(f"CI GUARD  |  FLAGSMITH PROJECT #{client.project}  |  {len(environments)} ENVIRONMENTS")
    print("  ".join(col.ljust(n) for col, n in zip(rows[0], widths)).rstrip())
    print("  ".join("─" * n for n in widths))
    for row in rows[1:]:
        print("  ".join(col.ljust(n) for col, n in zip(row, widths)).rstrip())
    if not names:
        print("No matching flags.")
    print("RO = read-only. Targeted overrides may produce different results for individual users.")


def main(argv=None, *, extra_commands=None):
    parser = argparse.ArgumentParser(description="CI Guard terminal dashboard for self-hosted Flagsmith.")
    parser.add_argument("command", choices=("dashboard", "list"), nargs="?", default="dashboard")
    parser.add_argument("--filter", default="", help="Show only flag names containing this text.")
    parser.add_argument("--config", type=Path, default=ROOT / ".local/flagsmith.env", help="Private local config file.")
    parser.add_argument("--write-env", type=int, action="append", default=[], metavar="ID",
                        help="Opt an environment ID in to confirmed edits. May repeat. Production stays locked.")
    args = parser.parse_args(argv)
    client = None
    try:
        config = {}
        if args.config.exists():
            config = dict(line.split("=", 1) for line in args.config.read_text().splitlines()
                          if "=" in line and not line.lstrip().startswith("#"))
        def get(name, default=""):
            return os.environ.get(name, config.get(name, default)).strip()
        project = get("FLAGSMITH_PROJECT_ID")
        if not re.fullmatch(r"[0-9]{1,12}", project):
            raise TerminalError("Set FLAGSMITH_PROJECT_ID to the numeric project ID.")
        client = FlagsmithTerminal(get("FLAGSMITH_ADMIN_API_URL") or get("FLAGSMITH_API_URL"),
                                   get("FLAGSMITH_ADMIN_API_KEY"), int(project), args.write_env,
                                   scheme=get("FLAGSMITH_ADMIN_AUTH_SCHEME", "Api-Key"))
        show(client, args.filter)
        if args.command == "list":
            return 0
        commands = extra_commands or {}
        usage = "refresh [filter] | set ENV_ID FLAG VALUE | " + "".join(name + " | " for name in commands) + "quit"
        print(f"\nEnter: {usage}")
        while True:
            try:
                parts = shlex.split(input("ciguard> "))
                if not parts:
                    continue
                if parts[0] in ("q", "quit", "exit"):
                    return 0
                if parts[0] == "refresh" and len(parts) <= 2:
                    show(client, parts[1] if len(parts) > 1 else args.filter)
                elif parts[0] == "set" and len(parts) == 4 and re.fullmatch(r"[0-9]{1,12}", parts[1]):
                    change = client.preview(int(parts[1]), parts[2], parts[3])
                    result = confirm_and_apply(client, change)
                    print(f"Saved and verified in Flagsmith: {result.display(change.name)}" if result else "Cancelled. No write.")
                elif len(parts) == 1 and parts[0] in commands:
                    commands[parts[0]]()
                else:
                    print(f"Use: {usage}")
            except (TerminalError, ValueError) as exc:
                print(safe(str(exc)), file=sys.stderr)
    except (EOFError, KeyboardInterrupt):
        return 0
    except (TerminalError, OSError, UnicodeError) as exc:
        print(str(exc) if isinstance(exc, TerminalError) else "Could not read the private configuration.", file=sys.stderr)
        return 1
    finally:
        if client:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
