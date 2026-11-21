"""Bounded subprocess capture and conservative, literal error classification."""

from __future__ import annotations

import codecs
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import tempfile
import time

from .pytest_support import classify as classify_pytest, instrument, pytest_argument_index, read_outcomes
from .report import StreamingRedactor


OUTPUT_LIMIT = 16_384
ASSERTION = re.compile(r"\bAssertionError\b|\bDID NOT (?:RAISE|WARN)\b|^\s*[E>]\s+assert\b|^FAILED[^\n]* - assert\b", re.MULTILINE)


class Cancelled(BaseException):
    def __init__(self, signum):
        self.signum = signum
        self.attempt = None


class OutputCapture:
    def __init__(self, patterns: list[str]):
        self.patterns = patterns
        self.text = ""
        self.truncated = False
        self.assertion = False
        self.signature = None
        self.evidence = ""
        self.overlap = ""
        self.overlap_size = max([512, *(len(pattern) for pattern in patterns)])

    def feed(self, text: str) -> None:
        searchable = self.overlap + text
        assertion = ASSERTION.search(searchable)
        if assertion and not self.assertion:
            self.assertion = True
            self.evidence = searchable[max(0, assertion.start() - 100):assertion.end() + 500]
        if not self.signature:
            for pattern in self.patterns:
                if pattern in searchable:
                    self.signature = pattern
                    break
        self.overlap = searchable[-self.overlap_size:]
        self.text += text
        if len(self.text) > OUTPUT_LIMIT:
            self.truncated = True
            self.text = self.text[-OUTPUT_LIMIT:]


def _read_output(stream, capture: OutputCapture) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    redactor = StreamingRedactor()
    try:
        while data := os.read(stream.fileno(), 4096):
            capture.feed(redactor.feed(decoder.decode(data)))
        capture.feed(redactor.feed(decoder.decode(b"", final=True), final=True))
    except (OSError, ValueError):
        # Closing the descriptor after the process group is killed ends capture.
        pass


def _terminate_group(process: subprocess.Popen) -> None:
    """Terminate the whole session, including children whose parent exited."""
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=0.3)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    else:
        # taskkill /T terminates descendants; argv is never interpreted by a shell.
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if process.poll() is None:
            process.kill()
    process.wait(timeout=3)


def execute(argv: list[str], timeout: float, patterns: list[str]) -> dict:
    pytest_index = pytest_argument_index(argv)
    if pytest_index is None:
        return _execute(argv, timeout, patterns)
    with tempfile.TemporaryDirectory(prefix="ci-guard-pytest-") as directory:
        report_path = Path(directory) / "outcomes.json"
        command, env = instrument(argv, pytest_index, report_path)
        return _execute(command, timeout, patterns, env=env, pytest_report=report_path)


def _execute(argv: list[str], timeout: float, patterns: list[str], *, env=None, pytest_report=None) -> dict:
    started = time.monotonic()
    options = {"start_new_session": True} if os.name == "posix" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    stdout, stderr = OutputCapture(patterns), OutputCapture(patterns)
    threads = []
    timed_out = False
    interrupted = None
    pending_signals, previous_handlers = [], {}

    def defer_signal(signum, frame):
        pending_signals.append(signum)

    def restore_handlers():
        for signum, handler in previous_handlers.items():
            if signal.getsignal(signum) is defer_signal:
                signal.signal(signum, handler)

    # Defer cancellation only while acquiring the process handle. Python handlers
    # reset on exec, so the child does not inherit a blocked SIGTERM signal mask.
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, defer_signal)
    try:
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, shell=False, env=env, **options)
    except BaseException:
        restore_handlers()
        raise
    try:
        try:
            restore_handlers()
            for signum in pending_signals:
                handler = previous_handlers[signum]
                if callable(handler):
                    handler(signum, None)
                elif handler != signal.SIG_IGN:
                    raise Cancelled(signum)
            threads = [threading.Thread(target=_read_output, args=(stream, capture), daemon=True)
                       for stream, capture in ((process.stdout, stdout), (process.stderr, stderr))]
            for thread in threads:
                thread.start()
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
        except Cancelled as exc:
            interrupted = exc
        finally:
            _terminate_group(process)
    finally:
        restore_handlers()
        for thread in threads:
            if thread.ident is not None:
                thread.join(timeout=1)
        process.stdout.close()
        process.stderr.close()
    duration = time.monotonic() - started
    assertion_capture = next((item for item in (stdout, stderr) if item.assertion), None)
    signature = stdout.signature or stderr.signature
    pytest_result = read_outcomes(pytest_report) if pytest_report else None
    if interrupted:
        classification, evidence = "cancelled", f"Command interrupted by signal {interrupted.signum}; process group terminated."
    elif timed_out:
        classification, evidence = "timeout", f"Command exceeded {timeout:g} seconds; process group terminated."
    elif pytest_report:
        classification, evidence = classify_pytest(pytest_result, process.returncode, patterns)
    elif process.returncode == 0:
        classification, evidence = "passed", "Command exited successfully; individual test outcomes were not verified."
    elif assertion_capture:
        classification, evidence = "assertion", assertion_capture.evidence
    elif signature:
        classification, evidence = "dependency", f"Matched configured literal dependency signature: {signature}"
    else:
        classification, evidence = "unknown", (stderr.text or stdout.text)[-1000:] or f"Command exited with code {process.returncode}."
    attempt = {"exit_code": 124 if timed_out else process.returncode, "process_exit_code": process.returncode,
               "duration_seconds": round(duration, 6), "classification": classification, "evidence": evidence,
               "stdout": stdout.text, "stderr": stderr.text,
               "output_truncated": stdout.truncated or stderr.truncated, "timed_out": timed_out}
    attempt["verification"] = "pytest" if pytest_report else "command_exit"
    if pytest_result:
        attempt["pytest_outcomes"] = pytest_result["outcomes"]
    if classification != "passed" and attempt["exit_code"] == 0:
        attempt["exit_code"] = 1
    if interrupted:
        attempt["exit_code"] = 128 + interrupted.signum
        interrupted.attempt = attempt
        raise interrupted
    return attempt
