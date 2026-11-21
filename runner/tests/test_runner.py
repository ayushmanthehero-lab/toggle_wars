"""Runner behavior is tested through actual command subprocesses and HTTP traffic."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


RUNNER_ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def coordinator(*, mode="enforce", initial="run", retry_limit=0, health="unhealthy",
                fail_attempt=False, fail_probe=False, would_block=False, probe_action="run", patterns=None):
    state = {"requests": [], "attempts": [], "finish": None}
    policy = {"mode": mode, "max_job_retries": retry_limit,
              "shared_dependency_retry_budget": retry_limit,
              "dependency_cooldown_seconds": 30, "source": "test", "fingerprint": "test", "error": None}

    def decision(action, **extra):
        return {"action": action, "reason": "Dependency unavailable" if action == "block" else "Test decision",
                "retry_after_seconds": 30, "shared_remaining": max(0, retry_limit - len(state["attempts"])),
                "would_block": would_block, "policy": policy, **extra}

    run = {"id": "run-test", "repository": "owner/repo", "dependency": "inventory",
           "job_id": "test", "workflow_run_id": "123", "status": "running", "attempt_count": 0,
           "prevented_attempts": 0, "would_prevent_attempts": 0, "duration_seconds": 0,
           "reason": "", "policy": policy, "demo": True, "created_at": "2026-11-21T00:00:00Z", "finished_at": None}
    dependency = {"id": "inventory", "name": "Inventory API", "health_url": "http://inventory/health",
                  "error_patterns": patterns if patterns is not None else ["InventoryUnavailable"],
                  "timeout_seconds": 3, "status": health, "retries_used": 0, "budget": retry_limit,
                  "next_probe_at": None, "last_checked_at": None, "active_runs": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.reply(None)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            self.reply(body)

        def reply(self, body):
            state["requests"].append((self.command, self.path, body, self.headers.get("Authorization")))
            code = 200
            if self.path == "/api/dependencies":
                payload = [dependency]
            elif self.path == "/api/runs":
                run["decision"] = decision(initial)
                if initial == "probe":
                    run["decision"].update(probe_token="lease-secret", probe_generation=7)
                if initial == "block":
                    run.update(prevented_attempts=1)
                payload = dict(run)
            elif self.path == "/api/dependencies/inventory/probe":
                if fail_probe:
                    code, payload = 503, {"detail": "Probe unavailable"}
                elif initial == "probe" and not state["attempts"]:
                    if body.get("probe_token") != "lease-secret" or body.get("probe_generation") != 7:
                        code, payload = 409, {"detail": "Recovery lease missing"}
                    else:
                        payload = {**dependency, "status": health, "detail": "HTTP 200" if health == "healthy" else "HTTP 503",
                                   "performed": True, "decision": decision(probe_action)}
                else:
                    payload = {**dependency, "status": health, "detail": "HTTP 503", "performed": True}
            elif self.path == "/api/runs/run-test/attempts":
                if fail_attempt:
                    code, payload = 503, {"detail": "Unavailable"}
                else:
                    state["attempts"].append(body)
                    run["attempt_count"] = len(state["attempts"])
                    run["duration_seconds"] += body["duration_seconds"]
                    if body["classification"] == "passed":
                        action = "stop"
                    elif body["classification"] == "dependency" and health == "unhealthy" and len(state["attempts"]) <= retry_limit:
                        action = "retry"
                    elif body["classification"] == "dependency" and health == "unhealthy" and mode == "enforce":
                        action = "block"
                        run["prevented_attempts"] += 1
                    else:
                        action = "stop"
                    if would_block:
                        run["would_prevent_attempts"] += 1
                    run["decision"] = decision(action)
                    payload = {"run": dict(run), "decision": run["decision"]}
            elif self.path == "/api/runs/run-test/finish":
                state["finish"] = body
                run.update(status=body["status"], reason=body.get("reason", ""))
                payload = dict(run)
            else:
                code, payload = 404, {"detail": "Not found"}
            data = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def invocation(tmp_path, server, code=None, *, timeout=5, extra=None, env_extra=None):
    report = tmp_path / "report.json"
    summary = tmp_path / "summary.md"
    outputs = tmp_path / "outputs.txt"
    env = {**os.environ, "PYTHONPATH": str(RUNNER_ROOT), "GITHUB_STEP_SUMMARY": str(summary), "GITHUB_OUTPUT": str(outputs)}
    env.pop("CI_GUARD_TOKEN", None)
    env.update(env_extra or {})
    argv = [sys.executable, "-m", "ci_guard_runner", "run", "--server", server,
            "--repository", "owner/repo", "--dependency", "inventory", "--job-id", "test",
            "--report", str(report), "--timeout", str(timeout), *(extra or [])]
    if code is not None:
        argv += ["--", sys.executable, "-c", code]
    return argv, env, report, summary, outputs


def run_command(tmp_path, server, code=None, **kwargs):
    argv, env, report, summary, outputs = invocation(tmp_path, server, code, **kwargs)
    result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=15)
    assert report.exists(), f"Runner must write a report on all exits: {result.stderr}"
    return result, json.loads(report.read_text()), summary.read_text(), outputs.read_text()


def test_actual_success_is_recorded_and_only_success_exits_zero(tmp_path):
    with coordinator() as (url, state):
        result, report, summary, outputs = run_command(tmp_path, url, "print('checks passed')")
    assert result.returncode == 0
    assert report["status"] == "passed"
    assert report["attempts"][0]["classification"] == "passed"
    assert report["actual_execution_seconds"] > 0
    assert state["finish"]["status"] == "passed"
    assert "passed" in summary.lower()
    assert "exit-code=0" in outputs


@pytest.mark.parametrize("code, expected", [
    ("raise AssertionError('InventoryUnavailable')", "assertion"),
    ("print('E   assert False, InventoryUnavailable'); raise SystemExit(1)", "assertion"),
    ("print('unrecognized failure'); raise SystemExit(9)", "unknown"),
])
def test_assertion_and_unknown_errors_fail_without_retry(tmp_path, code, expected):
    with coordinator(retry_limit=3) as (url, state):
        result, report, _, _ = run_command(tmp_path, url, code)
    assert result.returncode == 1
    assert report["status"] == "failed"
    assert [a["classification"] for a in report["attempts"]] == [expected]
    assert len(state["attempts"]) == 1
    assert not any(request[1].endswith("/probe") for request in state["requests"])


def test_known_dependency_uses_confirmed_health_and_reserved_retries(tmp_path):
    counter = tmp_path / "counter.txt"
    code = f"from pathlib import Path; p=Path({str(counter)!r}); p.write_text(p.read_text()+'x' if p.exists() else 'x'); print('InventoryUnavailable'); raise SystemExit(1)"
    with coordinator(retry_limit=1) as (url, state):
        result, report, summary, _ = run_command(tmp_path, url, code)
    assert result.returncode == 2
    assert counter.read_text() == "xx"
    assert report["status"] == "blocked"
    assert report["prevented_attempts"] == 1
    assert report["would_prevent_attempts"] == 0
    assert len(report["attempts"]) == 2
    assert all(a["health_status"] == "unhealthy" for a in state["attempts"])
    assert "InventoryUnavailable" in state["attempts"][0]["evidence"]
    assert "blocked" in summary.lower()
    assert "estimated" not in summary.lower() or "not estimated" in summary.lower()


def test_success_after_retry_is_actual_success(tmp_path):
    marker = tmp_path / "marker"
    code = f"from pathlib import Path; p=Path({str(marker)!r}); existed=p.exists(); p.touch(); print('recovered' if existed else 'InventoryUnavailable'); raise SystemExit(0 if existed else 1)"
    with coordinator(retry_limit=2) as (url, _):
        result, report, _, _ = run_command(tmp_path, url, code)
    assert result.returncode == 0
    assert [a["classification"] for a in report["attempts"]] == ["dependency", "passed"]


@pytest.mark.parametrize("health", ["healthy", "unknown"])
def test_signature_without_unhealthy_probe_never_retries(tmp_path, health):
    with coordinator(retry_limit=3, health=health) as (url, state):
        result, report, _, _ = run_command(tmp_path, url, "print('InventoryUnavailable'); raise SystemExit(1)")
    assert result.returncode == 1
    assert len(report["attempts"]) == 1
    assert state["attempts"][0]["health_status"] == health


def test_literal_error_patterns_are_not_regular_expressions(tmp_path):
    with coordinator(retry_limit=3, patterns=["inventory.*down"]) as (url, state):
        result, report, _, _ = run_command(tmp_path, url, "print('inventory is down'); raise SystemExit(1)")
    assert result.returncode == 1
    assert report["attempts"][0]["classification"] == "unknown"
    assert len(state["attempts"]) == 1


def test_blocked_initial_decision_never_executes_command(tmp_path):
    marker = tmp_path / "must-not-exist"
    with coordinator(initial="block") as (url, state):
        result, report, summary, outputs = run_command(tmp_path, url, f"from pathlib import Path; Path({str(marker)!r}).touch()")
    assert result.returncode == 2
    assert not marker.exists()
    assert report["status"] == "blocked"
    assert report["attempts"] == []
    assert report["actual_execution_seconds"] == 0
    assert "executed: 0" in summary.lower()
    assert "status=blocked" in outputs


def test_observe_would_blocks_are_not_counted_as_prevention(tmp_path):
    with coordinator(mode="observe", retry_limit=1, would_block=True) as (url, _):
        result, report, summary, _ = run_command(tmp_path, url, "print('InventoryUnavailable'); raise SystemExit(1)")
    assert result.returncode == 1
    assert len(report["attempts"]) == 2
    assert report["prevented_attempts"] == 0
    assert report["would_prevent_attempts"] == 2
    assert "prevented attempts: 0" in summary.lower()
    assert "would prevent" in summary.lower()


def test_off_mode_executes_exactly_once_even_with_retry_response(tmp_path):
    with coordinator(mode="off", retry_limit=3) as (url, _):
        result, report, _, _ = run_command(tmp_path, url, "print('InventoryUnavailable'); raise SystemExit(1)")
    assert result.returncode == 1
    assert len(report["attempts"]) == 1


@pytest.mark.parametrize("health, probe_action, expected_exit", [("healthy", "run", 0), ("unhealthy", "block", 2)])
def test_recovery_probe_passes_owned_lease_before_execution(tmp_path, health, probe_action, expected_exit):
    with coordinator(initial="probe", health=health, probe_action=probe_action) as (url, state):
        result, report, _, _ = run_command(tmp_path, url, "print('executed')")
    assert result.returncode == expected_exit
    assert len(report["attempts"]) == (1 if expected_exit == 0 else 0)
    probe = next(r for r in state["requests"] if r[1].endswith("/probe"))
    assert probe[2] == {"run_id": "run-test", "probe_token": "lease-secret", "probe_generation": 7}


def test_coordinator_connection_failure_reports_error_without_execution(tmp_path):
    marker = tmp_path / "must-not-exist"
    result, report, summary, _ = run_command(tmp_path, "http://127.0.0.1:1", f"from pathlib import Path; Path({str(marker)!r}).touch()")
    assert result.returncode == 3
    assert report["status"] == "error"
    assert not marker.exists()
    assert "coordina" in summary.lower()


@pytest.mark.parametrize("code, exit_code", [("raise SystemExit(8)", 1), ("print('pass')", 3)])
def test_failed_result_submission_never_turns_into_success(tmp_path, code, exit_code):
    with coordinator(fail_attempt=True, retry_limit=2) as (url, _):
        result, report, _, _ = run_command(tmp_path, url, code)
    assert result.returncode == exit_code
    assert len(report["attempts"]) == 1
    assert report["coordination_error"]


@pytest.mark.skipif(os.name != "posix", reason="Process group behavior is POSIX-specific")
def test_timeout_terminates_child_process_group_and_returns_124(tmp_path):
    child_marker = tmp_path / "child-survived"
    child = f"import time; from pathlib import Path; time.sleep(1.5); Path({str(child_marker)!r}).touch()"
    parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(10)"
    with coordinator(retry_limit=3) as (url, _):
        started = time.monotonic()
        result, report, _, _ = run_command(tmp_path, url, parent, timeout=0.2)
    assert result.returncode == 124
    assert report["attempts"][0]["classification"] == "timeout"
    assert time.monotonic() - started < 4
    time.sleep(1.5)
    assert not child_marker.exists()


def test_bounded_logs_preserve_early_assertion_evidence(tmp_path):
    code = "print('AssertionError: InventoryUnavailable'); print('x'*200000); raise SystemExit(1)"
    with coordinator(retry_limit=3) as (url, _):
        result, report, _, _ = run_command(tmp_path, url, code)
    attempt = report["attempts"][0]
    assert result.returncode == 1
    assert attempt["classification"] == "assertion"
    assert len(attempt["stdout"]) <= 17000
    assert attempt["output_truncated"] is True


def test_tokens_are_redacted_from_report_summary_and_api_evidence(tmp_path):
    token = "test-secret-token-123456"
    code = "import os; print('InventoryUnavailable ' + os.environ['CI_GUARD_TOKEN']); raise SystemExit(1)"
    with coordinator() as (url, state):
        result, report, summary, _ = run_command(tmp_path, url, code, env_extra={"CI_GUARD_TOKEN": token})
    assert result.returncode == 2
    assert token not in json.dumps(report)
    assert token not in summary
    assert token not in state["attempts"][0]["evidence"]
    assert all(request[3] == f"Bearer {token}" for request in state["requests"])


def test_command_string_uses_argv_without_shell_evaluation(tmp_path):
    marker = tmp_path / "shell-was-executed"
    literal = f"$(touch {marker})"
    import shlex
    command = shlex.join([sys.executable, "-c", "import sys; print(sys.argv[1])", literal])
    with coordinator() as (url, _):
        result, report, _, _ = run_command(tmp_path, url, extra=["--command-string", command])
    assert result.returncode == 0
    assert literal in report["attempts"][0]["stdout"]
    assert not marker.exists()


def test_configuration_error_still_writes_report_and_summary(tmp_path):
    result, report, summary, _ = run_command(tmp_path, "http://127.0.0.1:1", extra=["--command-string", "'unterminated"])
    assert result.returncode == 3
    assert report["status"] == "error"
    assert report["attempts"] == []
    assert "configuration" in summary.lower()


@pytest.mark.skipif(os.name != "posix", reason="Signals are POSIX-specific")
def test_signal_cancellation_writes_report_and_kills_descendants(tmp_path):
    marker = tmp_path / "started"
    child_marker = tmp_path / "child-survived"
    child = f"import time; from pathlib import Path; time.sleep(1.5); Path({str(child_marker)!r}).touch()"
    code = f"import subprocess,sys,time; from pathlib import Path; subprocess.Popen([sys.executable,'-c',{child!r}]); Path({str(marker)!r}).touch(); time.sleep(10)"
    with coordinator() as (url, state):
        argv, env, report_path, summary, outputs = invocation(tmp_path, url, code)
        proc = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline and proc.poll() is None:
                time.sleep(0.02)
            assert marker.exists(), proc.communicate(timeout=2)
            proc.send_signal(signal.SIGTERM)
            proc.communicate(timeout=5)
            report = json.loads(report_path.read_text())
            assert proc.returncode == 143
            assert report["status"] == "cancelled"
            assert report["actual_execution_seconds"] > 0
            assert len(report["attempts"]) == 1
            assert report["attempts"][0]["classification"] == "cancelled"
            assert state["finish"]["status"] == "cancelled"
            assert summary.exists() and outputs.exists()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
    time.sleep(1.5)
    assert not child_marker.exists()


def test_command_help_argument_does_not_suppress_reports(tmp_path):
    with coordinator() as (url, _):
        argv, env, report, summary, outputs = invocation(tmp_path, url, "import sys; print(sys.argv[1])")
        result = subprocess.run([*argv, "--help"], env=env, capture_output=True, timeout=5)
    assert result.returncode == 0
    assert report.exists() and summary.exists() and outputs.exists()


def test_missing_executable_finishes_registered_run_as_error(tmp_path):
    with coordinator() as (url, state):
        result, report, _, _ = run_command(tmp_path, url, extra=["--command-string", "/nonexistent-ci-guard-test-command"])
    assert result.returncode == 3
    assert report["status"] == "error"
    assert state["finish"]["status"] == "error"


def test_probe_coordination_error_still_records_executed_attempt_without_retry(tmp_path):
    with coordinator(fail_probe=True, retry_limit=3) as (url, state):
        result, report, _, _ = run_command(tmp_path, url, "print('InventoryUnavailable'); raise SystemExit(1)")
    assert result.returncode == 1
    assert report["coordination_error"]
    assert len(report["attempts"]) == 1
    assert len(state["attempts"]) == 1
    assert state["attempts"][0]["health_status"] == "unknown"


@pytest.mark.skipif(os.name != "posix", reason="Signals are POSIX-specific")
def test_timeout_cannot_submit_a_zero_exit_even_if_child_handles_sigterm(tmp_path):
    code = "import signal,time,sys; signal.signal(signal.SIGTERM,lambda *args:sys.exit(0)); time.sleep(10)"
    with coordinator() as (url, state):
        result, report, _, _ = run_command(tmp_path, url, code, timeout=0.2)
    assert result.returncode == 124
    assert state["attempts"][0]["exit_code"] == 124
    assert report["attempts"][0]["process_exit_code"] == 0


def test_command_metadata_redacts_secret_arguments_before_quoting(tmp_path):
    import shlex
    token = "credential-with-'quotes'"
    command = shlex.join([sys.executable, "-c", "print('ok')", token])
    with coordinator() as (url, state):
        result, _, _, _ = run_command(tmp_path, url, extra=["--command-string", command], env_extra={"CI_GUARD_TOKEN": token})
    assert result.returncode == 0
    create = next(request for request in state["requests"] if request[1] == "/api/runs")
    assert shlex.split(create[2]["command"])[-1] == "[REDACTED]"


@pytest.mark.parametrize("test_body, classification, expected_exit", [
    ('with pytest.raises(RuntimeError, match="InventoryUnavailable"):\n        pass', "assertion", 1),
    ('with pytest.warns(RuntimeWarning, match="InventoryUnavailable"):\n        pass', "assertion", 1),
    ('pytest.fail("unrelated failure")  # InventoryUnavailable is source text', "assertion", 1),
    ('raise ValueError("unrelated failure")  # InventoryUnavailable is source text', "unknown", 1),
    ('print("InventoryUnavailable"); raise ValueError("unrelated failure")', "unknown", 1),
    ('pytest.fail("InventoryUnavailable: inventory request failed")', "dependency", 2),
    ('raise RuntimeError("InventoryUnavailable: inventory request failed")', "dependency", 2),
])
def test_real_pytest_only_uses_actual_failure_evidence(tmp_path, test_body, classification, expected_exit):
    import shlex
    test_file = tmp_path / "test_classification.py"
    test_file.write_text(f"import pytest\n\ndef test_scenario():\n    {test_body}\n")
    command = shlex.join([sys.executable, "-m", "pytest", "-q", "--color=yes", str(test_file)])
    with coordinator() as (url, state):
        result, report, _, _ = run_command(tmp_path, url, extra=["--command-string", command])
    assert result.returncode == expected_exit
    assert [a["classification"] for a in report["attempts"]] == [classification]
    assert len(state["attempts"]) == 1
    assert any(request[1].endswith("/probe") for request in state["requests"]) == (classification == "dependency")


@pytest.mark.parametrize("second_body, classification", [
    ("assert False", "assertion"),
    ("raise ValueError('unrelated regression')", "unknown"),
])
def test_mixed_pytest_failures_never_retry_whole_suite(tmp_path, second_body, classification):
    import shlex
    test_file = tmp_path / "test_mixed.py"
    test_file.write_text("import pytest\n\ndef test_dependency():\n    pytest.fail('InventoryUnavailable')\n\n"
                         f"def test_regression():\n    {second_body}\n")
    command = shlex.join([sys.executable, "-m", "pytest", "-q", str(test_file)])
    with coordinator(retry_limit=3) as (url, state):
        result, report, _, _ = run_command(tmp_path, url, extra=["--command-string", command])
    assert result.returncode == 1
    assert [a["classification"] for a in report["attempts"]] == [classification]
    assert len(state["attempts"]) == 1


@pytest.mark.skipif(os.name != "posix", reason="Signals are POSIX-specific")
def test_cancellation_during_thread_setup_terminates_real_child(monkeypatch):
    from ci_guard_runner import process as runner_process
    spawned = []
    actual_popen = runner_process.subprocess.Popen

    def remember_process(*args, **kwargs):
        child = actual_popen(*args, **kwargs)
        spawned.append(child)
        return child

    def cancel_during_thread_start(thread):
        raise runner_process.Cancelled(signal.SIGTERM)

    monkeypatch.setattr(runner_process.subprocess, "Popen", remember_process)
    monkeypatch.setattr(runner_process.threading.Thread, "start", cancel_during_thread_start)
    try:
        with pytest.raises(runner_process.Cancelled):
            runner_process.execute([sys.executable, "-c", "import time; time.sleep(10)"], 20, [])
        assert spawned[0].poll() is not None, "Cancellation during setup must not leave the command running"
    finally:
        for child in spawned:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()


@pytest.mark.parametrize("source, pytest_args, expected_exit, passed, skipped", [
    ("import pytest\n@pytest.mark.skip(reason='unavailable')\ndef test_skipped(): pass\n", [], 1, 0, 1),
    ("def test_exists(): pass\n", ["--collect-only"], 1, 0, 0),
    ("# no tests\n", [], 1, 0, 0),
    ("import pytest\n@pytest.mark.xfail(reason='known failure')\ndef test_expected_failure(): assert False\n", [], 1, 0, 0),
    ("import pytest\ndef test_passed(): pass\n@pytest.mark.skip(reason='unavailable')\ndef test_skipped(): pass\n", [], 0, 1, 1),
])
def test_real_pytest_requires_passed_test_call_evidence(tmp_path, source, pytest_args, expected_exit, passed, skipped):
    import shlex
    test_file = tmp_path / "test_outcomes.py"
    test_file.write_text(source)
    command = shlex.join([sys.executable, "-m", "pytest", "-q", *pytest_args, str(test_file)])
    with coordinator(retry_limit=3) as (url, state):
        result, report, summary, _ = run_command(tmp_path, url, extra=["--command-string", command])
    assert result.returncode == expected_exit
    assert len(report["attempts"]) == 1
    attempt = report["attempts"][0]
    assert attempt["pytest_outcomes"]["passed"] == passed
    assert attempt["pytest_outcomes"]["skipped"] == skipped
    if expected_exit:
        assert attempt["classification"] == "unknown"
        assert state["attempts"][0]["exit_code"] != 0
        assert "no passed test call" in attempt["evidence"].lower()
    else:
        assert attempt["classification"] == "passed"
        assert "pytest" in summary.lower()


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_secret_is_redacted_before_output_tail_truncation(tmp_path, stream):
    token = "ghp_" + "R" * 32
    code = f"import os,sys; sys.{stream}.write(os.environ['REVIEW_TEST_TOKEN'] + 'x' * (16384-32))"
    with coordinator() as (url, _):
        result, report, summary, _ = run_command(tmp_path, url, code, env_extra={"REVIEW_TEST_TOKEN": token})
    assert result.returncode == 0
    assert "R" * 32 not in json.dumps(report)
    assert token not in summary
    assert len(report["attempts"][0][stream]) <= 16384


def test_secret_crossing_read_chunks_is_redacted_before_capture(tmp_path):
    token = "boundary-secret-123456789"
    code = "import os,sys,time; token=os.environ['REVIEW_TEST_TOKEN']; sys.stdout.write('x'*4090+token[:9]); sys.stdout.flush(); time.sleep(.03); sys.stdout.write(token[9:]+'y'*20000)"
    with coordinator() as (url, _):
        result, report, _, _ = run_command(tmp_path, url, code, env_extra={"REVIEW_TEST_TOKEN": token})
    assert result.returncode == 0
    assert token not in json.dumps(report)
    assert "secret-123456789" not in json.dumps(report)


@pytest.mark.skipif(os.name != "posix", reason="Signals are POSIX-specific")
def test_cancellation_while_acquiring_process_handle_does_not_orphan_child(monkeypatch):
    from ci_guard_runner import process as runner_process
    spawned = []
    actual_popen = runner_process.subprocess.Popen

    def interrupt_before_popen_returns(*args, **kwargs):
        child = actual_popen(*args, **kwargs)
        spawned.append(child)
        signal.raise_signal(signal.SIGTERM)
        return child

    def cancel(signum, frame):
        raise runner_process.Cancelled(signum)

    previous = signal.signal(signal.SIGTERM, cancel)
    monkeypatch.setattr(runner_process.subprocess, "Popen", interrupt_before_popen_returns)
    try:
        with pytest.raises(runner_process.Cancelled):
            runner_process.execute([sys.executable, "-c", "import time; time.sleep(10)"], 20, [])
        assert spawned[0].poll() is not None, "Cancellation while acquiring Popen must still reap the command"
    finally:
        signal.signal(signal.SIGTERM, previous)
        for child in spawned:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()


def test_stream_redaction_does_not_expose_overlapping_secret_prefixes(monkeypatch):
    from ci_guard_runner import report as reports
    monkeypatch.setattr(reports.os, "environ", {"CI_GUARD_TOKEN": "abcdefgh", "REVIEW_SECRET": "ghijkl"})
    stream = reports.StreamingRedactor()
    output = stream.feed("abcdefghijklmno") + stream.feed("", final=True)
    assert "abcdef" not in output
    assert "ghijkl" not in output


def test_stream_redaction_memory_is_bounded_for_repeated_overlapping_secrets(monkeypatch):
    from ci_guard_runner import report as reports
    monkeypatch.setattr(reports.os, "environ", {"CI_GUARD_TOKEN": "abcdefabcdef"})
    stream = reports.StreamingRedactor()
    output = ""
    for _ in range(16):
        output += stream.feed("abcdef" * 512)
        assert len(stream.pending) <= 11
    output += stream.feed("", final=True)
    assert "abcdef" not in output
