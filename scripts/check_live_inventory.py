"""Release smoke test using the existing CI Guard runner and live Flagsmith policy."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

if __package__:
    from .inventory_check_support import (
        INVENTORY_SIGNATURE, PYTEST_COMMAND, RUNNER_ROOT, all_cases_passed,
        clean_test_environment, initial_report, outcome_counts, run_bounded, write_result,
    )
else:
    from inventory_check_support import (
        INVENTORY_SIGNATURE, PYTEST_COMMAND, RUNNER_ROOT, all_cases_passed,
        clean_test_environment, initial_report, outcome_counts, run_bounded, write_result,
    )

sys.path.insert(0, str(RUNNER_ROOT))
from ci_guard_runner.client import CoordinationError, Coordinator


LIVE_MODES = {"enforce", "observe"}
REQUIRED = ("CI_GUARD_URL", "CI_GUARD_PROJECT_TOKEN", "CI_GUARD_DEPENDENCY", "CI_GUARD_LIVE_INVENTORY_URL")
KNOWN_FAILURES = {1: "failed", 2: "blocked", 3: "error", 124: "failed", 130: "cancelled", 143: "cancelled"}


class InvalidConfiguration(ValueError):
    pass


def https_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or "?" in value or "#" in value
            or "\\" in value or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("Malformed HTTPS URL")
    _ = parsed.port
    return value.rstrip("/")


def configuration(repository: str | None) -> tuple[str, str, str, str, str, str]:
    missing = [key for key in REQUIRED if not os.getenv(key, "").strip()]
    if not repository:
        missing.append("GITHUB_REPOSITORY or --repository")
    if missing:
        raise InvalidConfiguration("Missing live configuration: " + ", ".join(missing) + ".")
    server_url, token, dependency, inventory_url = (os.environ[key] for key in REQUIRED)
    try:
        server_url, inventory_url = https_endpoint(server_url), https_endpoint(inventory_url)
    except ValueError:
        raise InvalidConfiguration("Live coordinator and inventory URLs must be explicit HTTPS URLs without credentials, query, or fragment.") from None
    if not re.fullmatch(r"cg_proj_[A-Za-z0-9_-]{1,247}", token):
        raise InvalidConfiguration("CI_GUARD_PROJECT_TOKEN must be a project-scoped runner token.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", dependency):
        raise InvalidConfiguration("CI_GUARD_DEPENDENCY must be a registered dependency identifier.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) or len(repository) > 200:
        raise InvalidConfiguration("Live smoke repository must be an owner/name identifier.")
    mode = os.getenv("CI_GUARD_EXPECTED_MODE", "enforce")
    if mode not in LIVE_MODES:
        raise InvalidConfiguration("CI_GUARD_EXPECTED_MODE must be enforce or observe.")
    return server_url, token, dependency, inventory_url, repository, mode


def verified_policy(policy, expected_mode: str) -> bool:
    return (expected_mode in LIVE_MODES and isinstance(policy, dict)
            and policy.get("source") == "flagsmith" and policy.get("mode") == expected_mode
            and policy.get("error", True) is None
            and isinstance(policy.get("fingerprint"), str)
            and re.fullmatch(r"[0-9a-f]{64}", policy["fingerprint"]) is not None)


def verify_guard_success(guard_result, expected_mode: str) -> bool:
    """Check the snapshots which admitted the command as well as terminal evidence."""
    if (not isinstance(guard_result, dict) or guard_result.get("status") != "passed"
            or type(guard_result.get("exit_code")) is not int or guard_result["exit_code"] != 0
            or guard_result.get("coordination_error", True) is not None
            or not verified_policy(guard_result.get("policy"), expected_mode)):
        return False
    decisions, attempts = guard_result.get("decisions"), guard_result.get("attempts")
    if (not isinstance(decisions, list)
            or not all(isinstance(decision, dict) and verified_policy(decision.get("policy"), expected_mode)
                       for decision in decisions)
            or [decision.get("action") for decision in decisions] not in (["run", "stop"], ["probe", "run", "stop"])
            or not isinstance(attempts, list) or len(attempts) != 1 or not isinstance(attempts[0], dict)):
        return False
    attempt = attempts[0]
    return (attempt.get("verification") == "pytest" and attempt.get("classification") == "passed"
            and type(attempt.get("exit_code")) is int and attempt["exit_code"] == 0
            and all_cases_passed(outcome_counts(attempt.get("pytest_outcomes"))))


def verify_registered_target(server_url: str, token: str, dependency: str, inventory_url: str) -> None:
    client = Coordinator(server_url, token)
    try:
        dependencies = client.request("GET", "/api/dependencies")
    finally:
        client.close()
    selected = next((item for item in dependencies if isinstance(item, dict) and item.get("id") == dependency), None)
    if not selected:
        raise InvalidConfiguration("The live dependency is not registered or not accessible to this project token.")
    if selected.get("health_url") != inventory_url + "/health":
        raise InvalidConfiguration("The registered dependency health URL must match the live inventory URL plus /health.")
    signatures = selected.get("error_patterns")
    if not isinstance(signatures, list) or INVENTORY_SIGNATURE not in signatures:
        raise InvalidConfiguration("The registered live dependency must include the inventory test's error signature.")


def run_check(report: dict, repository: str | None) -> None:
    server_url, token, dependency, inventory_url, repository, mode = configuration(repository)
    report["expected_mode"] = mode
    verify_registered_target(server_url, token, dependency, inventory_url)
    with TemporaryDirectory(prefix="ci-guard-live-") as work:
        raw_path = Path(work) / "guard-result.json"
        env = {
            **clean_test_environment(), "CI_GUARD_TOKEN": token, "INVENTORY_URL": inventory_url,
            **{key: os.environ[key] for key in ("SSL_CERT_FILE", "SSL_CERT_DIR", "HTTPS_PROXY",
                "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy")
               if key in os.environ},
        }
        command = [
            sys.executable, "-m", "ci_guard_runner", "run", "--server", server_url,
            "--repository", repository, "--dependency", dependency, "--job-id", "live-inventory-smoke",
            "--report", str(raw_path), "--timeout", "20", "--", *PYTEST_COMMAND,
        ]
        if workflow_run_id := os.getenv("GITHUB_RUN_ID"):
            if workflow_run_id.isdecimal():
                command[command.index("--report"):command.index("--report")] = ["--workflow-run-id", workflow_run_id]
        try:
            code = run_bounded(command, env, timeout=120)
        except subprocess.TimeoutExpired:
            report.update(status="failed", exit_code=124, reason="The guarded live smoke exceeded its time limit.")
            return
        raw = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.is_file() else None
        if isinstance(raw, dict):
            attempts = raw.get("attempts")
            if isinstance(attempts, list):
                report["attempts"] = len(attempts)
                if attempts and isinstance(attempts[-1], dict):
                    report["pytest_outcomes"] = outcome_counts(attempts[-1].get("pytest_outcomes"))
        if code != 0:
            report.update(status=KNOWN_FAILURES.get(code, "error"),
                          exit_code=code if code in KNOWN_FAILURES else 3,
                          reason="The guarded live inventory check did not pass.")
        elif verify_guard_success(raw, mode):
            report.update(status="passed", exit_code=0, policy_source="flagsmith",
                          reason="Actual live pytest cases and the applied Flagsmith policy passed verification.")
        else:
            report["reason"] = "Live smoke could not verify real pytest success and the expected applied Flagsmith policy."


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--repository", default=os.getenv("GITHUB_REPOSITORY"))
    args = parser.parse_args(argv)
    report = initial_report("live_release")
    try:
        run_check(report, args.repository)
    except InvalidConfiguration as error:
        report["reason"] = str(error)  # All messages are fixed text or environment field names.
    except CoordinationError:
        report["reason"] = "The live coordinator could not verify the registered dependency."
    except (OSError, ValueError, ImportError):
        report["reason"] = "The guarded live inventory check could not start or produce valid evidence."
    return write_result(args.report, report)


if __name__ == "__main__":
    raise SystemExit(main())
