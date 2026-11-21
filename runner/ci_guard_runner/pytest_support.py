"""Recognize direct pytest invocations and validate their private outcome report."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re


def pytest_argument_index(argv: list[str]) -> int | None:
    name = Path(argv[0]).name.lower()
    if name in {"pytest", "py.test", "pytest.exe", "py.test.exe"}:
        return 1
    if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?", name):
        for index, argument in enumerate(argv[1:], 1):
            if argument in {"-c", "--"}:
                return None
            if argument == "-m":
                return index + 2 if argv[index + 1:index + 2] == ["pytest"] else None
    return None


def instrument(argv: list[str], index: int, report_path: Path):
    command = [*argv[:index], "-p", "ci_guard_runner.pytest_plugin", *argv[index:]]
    env = dict(os.environ)
    package_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = package_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["CI_GUARD_PYTEST_REPORT"] = str(report_path)
    return command, env


def read_outcomes(path: Path) -> dict | None:
    try:
        if path.stat().st_size > 1_048_576:
            return None
        result = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(result, dict) or result.get("version") != 1:
            return None
        counts = result.get("outcomes")
        if not isinstance(counts, dict) or any(type(counts.get(key)) is not int or counts[key] < 0 for key in
                                               ("collected", "passed", "failed", "errors", "skipped", "xfailed", "xpassed", "deselected")):
            return None
        failures = result.get("failures")
        if not isinstance(failures, list) or len(failures) > 200 or any(not isinstance(item, dict) or not isinstance(item.get("message"), str) for item in failures):
            return None
        return result
    except (OSError, ValueError, TypeError):
        return None


def classify(result: dict | None, exit_code: int, patterns: list[str]) -> tuple[str, str]:
    if result is None:
        return "unknown", "Structured pytest outcome report unavailable; successful test execution could not be verified."
    counts = result["outcomes"]
    failures = result["failures"]
    if failures:
        classified = []
        for failure in failures:
            message = failure["message"]
            if failure.get("kind") == "assertion" or any(marker in message for marker in ("DID NOT RAISE", "DID NOT WARN", "[XPASS(strict)]")):
                category = "assertion"
            elif failure.get("kind") == "unknown":
                category = "unknown"
            elif any(pattern in message for pattern in patterns):
                category = "dependency"
            elif failure.get("kind") == "pytest_failure":
                category = "assertion"
            else:
                category = "unknown"
            classified.append((category, message[:1000]))
        for category in ("assertion", "unknown"):
            if matching := next((item for item in classified if item[0] == category), None):
                return matching
        if result.get("failure_overflow"):
            return "unknown", "Pytest failure evidence exceeded its bound; retries disabled."
        return classified[0]
    if counts["passed"] + counts["xpassed"] == 0:
        return "unknown", (f"Pytest recorded no passed test call: {counts['collected']} collected, "
                           f"{counts['skipped']} skipped, {counts['xfailed']} expected failures. Unexecuted tests cannot pass.")
    if exit_code != 0 or counts["failed"] or counts["errors"] or result.get("failure_overflow"):
        return "unknown", "Pytest exited unsuccessfully without complete failure evidence; retries disabled."
    return "passed", f"Pytest verified {counts['passed'] + counts['xpassed']} passed test call(s); {counts['skipped']} skipped."
