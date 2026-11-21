"""Explicitly loaded pytest adapter; never imported for arbitrary commands."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from .report import Redactor


class OutcomeRecorder:
    def __init__(self):
        self.counts = {name: 0 for name in ("collected", "passed", "failed", "errors", "skipped", "xfailed", "xpassed", "deselected")}
        self.failures = []
        self.failure_overflow = False
        self.redactor = Redactor()

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        result = yield
        report = result.get_result()
        if report.failed and call.excinfo is not None:
            error = call.excinfo.value
            if isinstance(error, AssertionError):
                kind = "assertion"
            elif isinstance(error, pytest.fail.Exception):
                kind = "pytest_failure"
            else:
                kind = "exception"
            report.ci_guard_failure = {"kind": kind, "message": self.redactor.text(str(error))[:4000]}

    def _record_failure(self, failure):
        if len(self.failures) < 200:
            self.failures.append(failure)
        else:
            self.failure_overflow = True

    def pytest_runtest_logreport(self, report):
        if report.failed:
            self.counts["failed" if report.when == "call" else "errors"] += 1
            self._record_failure(getattr(report, "ci_guard_failure", {"kind": "unknown", "message": "Pytest reported a failure without exception evidence."}))
        elif report.skipped:
            self.counts["xfailed" if hasattr(report, "wasxfail") else "skipped"] += 1
        elif report.when == "call" and report.passed:
            self.counts["xpassed" if hasattr(report, "wasxfail") else "passed"] += 1

    def pytest_collectreport(self, report):
        if report.failed:
            self.counts["errors"] += 1
            self._record_failure({"kind": "unknown", "message": "Pytest collection failed; no dependency retry is permitted."})
        elif report.skipped:
            self.counts["skipped"] += 1

    def pytest_deselected(self, items):
        self.counts["deselected"] += len(items)

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(self, session, exitstatus):
        self.counts["collected"] = session.testscollected
        destination = os.environ.get("CI_GUARD_PYTEST_REPORT")
        if destination:
            payload = {"version": 1, "exit_status": int(exitstatus), "outcomes": self.counts,
                       "failures": self.failures, "failure_overflow": self.failure_overflow}
            path = Path(destination)
            temporary = path.with_suffix(f".{os.getpid()}.tmp")
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            temporary.replace(path)


def pytest_configure(config):
    config.pluginmanager.register(OutcomeRecorder(), "ci_guard_outcome_recorder")
