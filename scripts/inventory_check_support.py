"""Shared evidence/report helpers for the two inventory test gates."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import NamedTemporaryFile


ROOT = Path(__file__).resolve().parents[1]
RUNNER_ROOT = ROOT / "runner"
INVENTORY_TEST = ROOT / "examples" / "test_inventory.py"
INVENTORY_SIGNATURE = "INVENTORY_DEPENDENCY_UNAVAILABLE"
PYTEST_COMMAND = [sys.executable, "-m", "pytest", str(INVENTORY_TEST), "-q"]
OUTCOME_KEYS = ("collected", "passed", "failed", "errors", "skipped", "xfailed", "xpassed", "deselected")


def clean_test_environment() -> dict[str, str]:
    """Do not inherit test-selection arguments, third-party plugins, or secrets."""
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "PYTHONPATH": str(RUNNER_ROOT),
        "PYTHONIOENCODING": "utf-8",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }


def run_bounded(command: list[str], env: dict[str, str], timeout: float) -> int:
    """Do not expose test logs or kill the runner before it can clean up."""
    with subprocess.Popen(command, env=env, cwd=ROOT, stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as process:
        try:
            return process.wait(timeout=timeout)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise


def outcome_counts(value) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    if any(type(value.get(key)) is not int or value[key] < 0 for key in OUTCOME_KEYS):
        return None
    return {key: value[key] for key in OUTCOME_KEYS}


def all_cases_passed(counts: dict[str, int] | None) -> bool:
    return bool(counts and counts["collected"] == counts["passed"] > 0
                and not any(counts[key] for key in OUTCOME_KEYS if key not in {"passed", "collected"}))


def initial_report(scope: str) -> dict:
    return {"schema_version": "1.0", "scope": scope, "status": "error", "exit_code": 3,
            "reason": "The required test execution could not be verified.", "attempts": 0,
            "pytest_outcomes": None}


def write_result(path: Path, report: dict) -> int:
    """Publish only allowlisted evidence assembled by the gate, not raw runner logs."""
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as destination:
            temporary = Path(destination.name)
            json.dump(report, destination, indent=2, sort_keys=True)
            destination.write("\n")
        os.replace(temporary, path)
    except (OSError, ValueError):
        print("CI Guard inventory gate: could not write the required report.", file=sys.stderr)
        return 3
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"CI Guard {report['scope']}: {report['status']} (exit {report['exit_code']}). {report['reason']}")
    return report["exit_code"]
