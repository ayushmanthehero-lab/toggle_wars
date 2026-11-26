"""Check a real Flagsmith environment and prepare a reviewable GitHub workflow."""
from __future__ import annotations

import argparse
import ipaddress
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

import httpx

if __package__:
    from .flagsmith_terminal import FlagsmithTerminal, TerminalError
else:
    from flagsmith_terminal import FlagsmithTerminal, TerminalError

from ci_guard.policy import POLICY_FIELDS, Policy, PolicyProvider
from pydantic import ValidationError

TEMPLATE = Path(__file__).resolve().parents[1] / "infra/starter.workflow.yml"


class SetupError(ValueError):
    """An actionable, credential-free onboarding error."""


def public_https(value):
    try:
        parts = urlsplit(value)
        if (parts.scheme != "https" or not parts.hostname or parts.username is not None
                or parts.password is not None or any(c in value for c in ("?", "#", "\\"))
                or any(c.isspace() or not c.isprintable() for c in value)
                or parts.hostname.rstrip(".").casefold() == "localhost"):
            raise ValueError
        _ = parts.port
        try:
            addr = ipaddress.ip_address(parts.hostname)
        except ValueError:
            pass  # A staging hostname can be reachable only on an approved runner.
        else:
            if addr.is_loopback or addr.is_unspecified:
                raise ValueError
    except ValueError:
        raise SetupError("Use a reachable HTTPS URL with no credentials, query, or fragment.") from None
    return value.rstrip("/")


def get_json(client, path, label, **kwargs):
    try:
        response = client.get(path, **kwargs)
        if response.status_code in (401, 403):
            raise SetupError(f"{label} denied this repo or token (HTTP {response.status_code}).")
        if not response.is_success:
            raise SetupError(f"{label} returned HTTP {response.status_code}. Check its URL and readiness.")
        return response.json()
    except SetupError:
        raise
    except (httpx.HTTPError, ValueError):
        raise SetupError(f"{label} could not be read. Check network access and TLS.") from None


def policy_fields(data):
    try:
        if not isinstance(data, dict) or data.get("source") != "flagsmith" or data.get("error", True) is not None:
            raise ValueError
        values = {key: data[key] for key in POLICY_FIELDS}
        Policy(**values)
        if values["mode"] not in ("observe", "enforce"):
            raise ValueError
        return values
    except (KeyError, ValidationError, ValueError, TypeError):
        raise SetupError("Expected a healthy Observe or Enforce policy from Flagsmith; check the four CI Guard flags.") from None


def check_coordinator(client, repository, dependency, selected):
    expected = policy_fields(selected)
    deps = get_json(client, "api/dependencies", "Coordinator dependency list")
    if not isinstance(deps, list) or not any(isinstance(d, dict) and d.get("id") == dependency for d in deps):
        raise SetupError("Dependency is not visible to this project token. Register it and check the token's allowlist.")
    current = get_json(client, "api/policy", "Coordinator policy", params={"repository": repository})
    if policy_fields(current) != expected:
        raise SetupError("Coordinator policy does not match the selected Flagsmith environment for this repo.")
    return expected["mode"]


def starter():
    return TEMPLATE.read_text(encoding="utf-8")


def doctor(*, flagsmith_url, flagsmith_token, scheme, project, environment, coordinator_url,
           project_token, repository, dependency):
    terminal = FlagsmithTerminal(flagsmith_url, flagsmith_token, project, set(), scheme=scheme)
    try:
        selected = terminal._environments().get(environment)
    finally:
        terminal.close()
    if selected is None:
        raise SetupError("Environment ID was not found in the selected Flagsmith project. Use the numeric ID from the terminal.")
    # The environment key is obtained by a Management API GET. Evaluate precisely
    # the same identity and traits as the coordinator (not just the default flags).
    provider = PolicyProvider(None)
    provider.api_url = flagsmith_url.rstrip("/") + "/"
    provider.environment_key = selected.key
    selected_policy = provider.evaluate(repository).model_dump()
    with httpx.Client(base_url=coordinator_url.rstrip("/") + "/", timeout=8, follow_redirects=False,
                      trust_env=False, headers={"Authorization": f"Bearer {project_token}"}) as client:
        return check_coordinator(client, repository, dependency, selected_policy)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="GitHub owner/name.")
    parser.add_argument("--dependency", required=True, help="Registered dependency ID.")
    parser.add_argument("--environment", required=True, type=int, help="Numeric Flagsmith environment ID.")
    parser.add_argument("--emit-workflow", action="store_true", help="Print starter YAML only after a successful check.")
    args = parser.parse_args(argv)
    try:
        required = ("FLAGSMITH_ADMIN_API_URL", "FLAGSMITH_ADMIN_API_KEY", "FLAGSMITH_PROJECT_ID",
                    "CI_GUARD_URL", "CI_GUARD_PROJECT_TOKEN")
        if missing := [key for key in required if not os.getenv(key, "").strip()]:
            raise SetupError("Missing configuration: " + ", ".join(missing) + ". See docs/QUICKSTART.md.")
        if (re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo) is None or len(args.repo) > 200
                or re.fullmatch(r"[A-Za-z0-9_-]{1,100}", args.dependency) is None or args.environment < 1):
            raise SetupError("Use an owner/name repository, a valid dependency ID, and a positive environment ID.")
        project = os.environ["FLAGSMITH_PROJECT_ID"]
        token = os.environ["CI_GUARD_PROJECT_TOKEN"]
        if re.fullmatch(r"[1-9][0-9]{0,11}", project) is None:
            raise SetupError("FLAGSMITH_PROJECT_ID must be the numeric project ID.")
        if re.fullmatch(r"cg_proj_[A-Za-z0-9_-]{1,247}", token) is None:
            raise SetupError("CI_GUARD_PROJECT_TOKEN must be a project-scoped runner token, not an admin token.")
        mode = doctor(flagsmith_url=public_https(os.environ["FLAGSMITH_ADMIN_API_URL"]),
                      flagsmith_token=os.environ["FLAGSMITH_ADMIN_API_KEY"],
                      scheme=os.getenv("FLAGSMITH_ADMIN_AUTH_SCHEME") or "Api-Key",
                      project=int(project), environment=args.environment,
                      coordinator_url=public_https(os.environ["CI_GUARD_URL"]), project_token=token,
                      repository=args.repo, dependency=args.dependency)
        print(f"Setup check passed: Flagsmith environment {args.environment}, scoped dependency, and matching {mode} policy.",
              file=sys.stderr)
        print(f"Set CI_GUARD_EXPECTED_MODE={mode} in GitHub's ci-guard-staging environment. No remote changes made.",
              file=sys.stderr)
        if args.emit_workflow:
            sys.stdout.write(starter())
    except (SetupError, TerminalError) as exc:
        print(f"Setup stopped: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print("Setup stopped: could not read the local starter or configuration.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
