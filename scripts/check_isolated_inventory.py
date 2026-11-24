"""Required PR integration check using a fresh, local inventory service."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import time

import httpx

# Allow both direct script invocation and import from tests.
if __package__:
    from .inventory_check_support import (
        PYTEST_COMMAND, ROOT, RUNNER_ROOT, all_cases_passed, clean_test_environment,
        initial_report, outcome_counts, run_bounded, write_result,
    )
else:
    from inventory_check_support import (
        PYTEST_COMMAND, ROOT, RUNNER_ROOT, all_cases_passed, clean_test_environment,
        initial_report, outcome_counts, run_bounded, write_result,
    )

sys.path[:0] = [str(ROOT), str(RUNNER_ROOT)]
from ci_guard_runner.pytest_support import read_outcomes
from examples.inventory_service import Handler, InventoryServer


@contextmanager
def running_inventory():
    with InventoryServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        thread.start()
        try:
            yield server.server_port
        finally:
            server.shutdown()
            thread.join(timeout=1)


def wait_for_health(inventory_url: str, *, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    with httpx.Client(follow_redirects=False, trust_env=False) as client:
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                response = client.get(inventory_url + "/health", timeout=min(remaining, 0.4))
                if response.status_code == 200 and response.json().get("status") == "healthy":
                    return True
            except (httpx.HTTPError, ValueError, AttributeError):
                pass
            time.sleep(max(0, min(0.05, deadline - time.monotonic())))
    return False


def run_check(report: dict, simulate_assertion_failure: bool) -> None:
    with running_inventory() as port, TemporaryDirectory(prefix="ci-guard-isolated-") as work:
        report["fixture_port"] = port
        url = f"http://127.0.0.1:{port}"
        if not wait_for_health(url):
            report["reason"] = "The isolated inventory fixture did not become healthy in time."
            return
        outcomes_path = Path(work) / "pytest.json"
        env = {
            **clean_test_environment(),
            "INVENTORY_URL": url,
            "CI_GUARD_PYTEST_REPORT": str(outcomes_path),
            "DEMO_ASSERTION_FAILURE": str(simulate_assertion_failure).lower(),
        }
        command = [*PYTEST_COMMAND, "-p", "ci_guard_runner.pytest_plugin"]
        report["attempts"] = 1
        try:
            code = run_bounded(command, env, timeout=30)
        except subprocess.TimeoutExpired:
            report.update(status="failed", exit_code=124, reason="The isolated pytest command timed out.")
            return
        outcome = read_outcomes(outcomes_path)
        counts = outcome_counts(outcome.get("outcomes") if outcome else None)
        report["pytest_outcomes"] = counts
        if code == 0 and outcome and outcome["exit_status"] == 0 and all_cases_passed(counts):
            report.update(status="passed", exit_code=0, reason="Actual pytest cases passed against the isolated inventory.")
        elif code == 1 and counts and counts["failed"] > 0:
            report.update(status="failed", exit_code=1, reason="An actual isolated inventory pytest case failed.")
        else:
            report["reason"] = "The isolated pytest run did not produce a complete passing result."


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--simulate-assertion-failure", action="store_true", help="Local demonstration only.")
    args = parser.parse_args(argv)
    report = initial_report("isolated_pr")
    try:
        run_check(report, args.simulate_assertion_failure)
    except (OSError, ValueError, RuntimeError, KeyError):
        report["reason"] = "The isolated inventory check could not start or complete."
    return write_result(args.report, report)


if __name__ == "__main__":
    raise SystemExit(main())
