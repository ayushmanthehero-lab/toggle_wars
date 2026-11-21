"""Reports state only observed execution and coordinator-confirmed decisions."""

from __future__ import annotations

from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import re
import tempfile


class Redactor:
    def __init__(self):
        self.secrets = sorted({value for key, value in os.environ.items()
                               if value and (key == "CI_GUARD_TOKEN" or
                                  (len(value) >= 6 and any(word in key.upper() for word in
                                   ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "PRIVATE_KEY", "ACCESS_KEY"))))}, key=len, reverse=True)

    def text(self, value: str) -> str:
        spans = []
        for secret in self.secrets:
            start = value.find(secret)
            while start >= 0:
                spans.append((start, start + len(secret)))
                start = value.find(secret, start + 1)
        if spans:
            merged = []
            for start, end in sorted(spans):
                if merged and start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
                else:
                    merged.append((start, end))
            pieces, previous = [], 0
            for start, end in merged:
                pieces.extend((value[previous:start], "[REDACTED]"))
                previous = end
            pieces.append(value[previous:])
            value = "".join(pieces)
        value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
        return value

    def clean(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {key: self.clean(item) for key, item in value.items() if key != "probe_token"}
        if isinstance(value, list):
            return [self.clean(item) for item in value]
        return value


class StreamingRedactor:
    """Hold secret-length overlap so chunking and tail trimming cannot reveal fragments."""

    def __init__(self):
        self.redactor = Redactor()
        self.pending = ""
        self.covered = bytearray()
        self.overlap = max([1, *(len(secret) for secret in self.redactor.secrets)]) - 1

    def feed(self, value: str, *, final: bool = False) -> str:
        self.pending += value
        self.covered.extend(bytes(len(value)))
        for secret in self.redactor.secrets:
            start = self.pending.find(secret)
            while start >= 0:
                self.covered[start:start + len(secret)] = b"\x01" * len(secret)
                start = self.pending.find(secret, start + 1)
        cut = len(self.pending) if final else max(0, len(self.pending) - self.overlap)
        pieces, position = [], 0
        while position < cut:
            sensitive = self.covered[position]
            end = self.covered.find(b"\x00" if sensitive else b"\x01", position + 1, cut)
            if end < 0:
                end = cut
            pieces.append("[REDACTED]" if sensitive else self.pending[position:end])
            position = end
        self.pending, self.covered = self.pending[cut:], self.covered[cut:]
        return self.redactor.text("".join(pieces))


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def markdown(report: dict) -> str:
    # Escape log-derived strings rather than allowing arbitrary HTML into summaries.
    safe = lambda value: html.escape(str(value)).replace("\r", "").replace("\n", " ").replace("|", "\\|")
    policy = report.get("policy") or {}
    lines = ["## CI Guard", "", f"**Result: {safe(report['status'])}** · exit code {report['exit_code']}", "",
             f"{safe(report.get('reason') or '')}", "",
             f"- Repository: {safe(report.get('repository') or 'not configured')}",
             f"- Dependency: {safe(report.get('dependency') or 'not configured')}",
             f"- Run: {safe(report.get('run_id') or 'not registered')}",
             f"- Policy: {safe(policy.get('mode', 'unknown'))} ({safe(policy.get('source', 'unavailable'))})",
             f"- Attempts executed: {len(report['attempts'])}",
             f"- Prevented attempts: {report['prevented_attempts']}",
             f"- Would prevent (observe only): {report['would_prevent_attempts']}",
             f"- Actual command execution: {report['actual_execution_seconds']:.3f} seconds", ""]
    if report.get("demo"):
        lines += ["Explicit local demo policy was used.", ""]
    if policy.get("error"):
        lines += [f"Policy unavailable: {safe(policy['error'])}. Retries disabled.", ""]
    if report.get("coordination_error"):
        lines += [f"Coordination error: {safe(report['coordination_error'])}", ""]
    if report["attempts"]:
        lines += ["| Attempt | Result | Exit | Seconds | Dependency health |", "| --- | --- | --- | --- | --- |"]
        for item in report["attempts"]:
            lines.append(f"| {item['attempt_number']} | {safe(item['classification'])} | {item['exit_code']} | {item['duration_seconds']:.3f} | {safe(item.get('health_status', 'unknown'))} |")
        lines += ["", "Evidence:", ""]
        for item in report["attempts"]:
            lines.append(f"- Attempt {item['attempt_number']}: {safe(item.get('evidence', ''))}")
            if counts := item.get("pytest_outcomes"):
                lines.append(f"  Pytest: {counts['collected']} collected; {counts['passed']} passed; {counts['failed']} failed; "
                             f"{counts['errors']} errors; {counts['skipped']} skipped; {counts['xfailed']} expected failures; {counts['xpassed']} unexpected passes.")
    if report["status"] == "blocked":
        lines += ["", "Tests did not pass. Check the registered dependency, restore its health, and rerun the workflow after the coordinator allows a recovery probe. This job does not wait for recovery."]
    lines += ["", "Execution time measures actual command runtime. Avoided time and cash savings are not estimated.", ""]
    return "\n".join(lines)


def write_reports(report: dict, report_path: str, redactor: Redactor) -> list[str]:
    """Write each destination independently so one unwritable sink cannot hide others."""
    errors = []
    report["finished_at"] = now()
    clean = redactor.clean(report)
    path = Path(report_path)
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".ci-guard-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(clean, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, path)
    except (OSError, ValueError) as exc:
        errors.append(f"Cannot write JSON report: {type(exc).__name__}.")
    finally:
        if temporary and temporary.exists():
            temporary.unlink(missing_ok=True)
    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        try:
            with open(summary_path, "a", encoding="utf-8") as stream:
                stream.write(markdown(clean))
        except (OSError, ValueError) as exc:
            errors.append(f"Cannot append GitHub summary: {type(exc).__name__}.")
    if output_path := os.environ.get("GITHUB_OUTPUT"):
        try:
            values = {"status": clean["status"], "run-id": clean.get("run_id") or "", "report": report_path, "exit-code": str(clean["exit_code"])}
            with open(output_path, "a", encoding="utf-8") as stream:
                for key, value in values.items():
                    stream.write(f"{key}={str(value).replace(chr(10), '').replace(chr(13), '')}\n")
        except (OSError, ValueError) as exc:
            errors.append(f"Cannot append GitHub outputs: {type(exc).__name__}.")
    return errors
