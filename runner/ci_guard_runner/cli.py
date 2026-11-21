"""The CLI keeps actual test results distinct from coordination decisions."""

from __future__ import annotations

import argparse
import math
import os
import shlex
import signal
import sys
from urllib.parse import quote
import uuid

from .client import Coordinator, CoordinationError
from .process import Cancelled, execute
from .report import Redactor, now, write_reports


class ConfigurationError(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ConfigurationError(message)


def parse_args(argv):
    parser = Parser(prog="ci-guard", description="Run tests with a shared, evidence-based dependency retry budget.")
    sub = parser.add_subparsers(dest="operation", required=True, parser_class=Parser)
    run = sub.add_parser("run")
    run.add_argument("--server", required=True)
    run.add_argument("--repository", required=True)
    run.add_argument("--dependency", required=True)
    run.add_argument("--job-id", default=os.environ.get("GITHUB_JOB", "local"))
    run.add_argument("--workflow-run-id", default=os.environ.get("GITHUB_RUN_ID"))
    run.add_argument("--report", default="ci-guard-report.json")
    run.add_argument("--timeout", type=float, default=120)
    run.add_argument("--command-string", help="Shell-style quoting parsed as argv; no shell expansion or pipelines.")
    run.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command[:1] == ["--"]:
        args.command = args.command[1:]
    if args.command_string is not None:
        if args.command:
            raise ConfigurationError("Use either --command-string or argv after --, not both.")
        try:
            args.command = shlex.split(args.command_string)
        except ValueError as exc:
            raise ConfigurationError(f"Invalid command quoting: {exc}") from exc
    if not args.command:
        raise ConfigurationError("A test command is required after -- or in --command-string.")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        raise ConfigurationError("Timeout must be a finite positive number of seconds.")
    if any("\x00" in argument for argument in args.command):
        raise ConfigurationError("Command arguments cannot contain null characters.")
    for value in (args.repository, args.dependency, args.job_id, args.report):
        if not value or any(ord(char) < 32 for char in value):
            raise ConfigurationError("Run identifiers and report paths must be nonempty and contain no control characters.")
    return args


def _initial_report():
    return {"schema_version": "1.0", "status": "error", "exit_code": 3,
            "created_at": now(), "run_id": None, "repository": None, "dependency": None,
            "job_id": None, "policy": None, "attempts": [], "decisions": [],
            "prevented_attempts": 0, "would_prevent_attempts": 0,
            "actual_execution_seconds": 0, "reason": "Runner did not execute tests.",
            "coordination_error": None, "demo": False}


def _decision(value):
    if not isinstance(value, dict) or value.get("action") not in {"run", "retry", "block", "stop", "probe"}:
        raise CoordinationError("Coordinator returned an invalid decision.")
    policy = value.get("policy")
    if not isinstance(policy, dict) or policy.get("mode") not in {"off", "observe", "enforce"}:
        raise CoordinationError("Coordinator returned an invalid policy.")
    retries = policy.get("max_job_retries")
    if type(retries) is not int or not 0 <= retries <= 100:
        raise CoordinationError("Coordinator returned an invalid per-job retry limit.")
    return value


def _update_run(report, run):
    if not isinstance(run, dict) or not isinstance(run.get("id"), str) or not run["id"]:
        raise CoordinationError("Coordinator returned an invalid run.")
    if report["run_id"] and report["run_id"] != run["id"]:
        raise CoordinationError("Coordinator returned a different run identifier.")
    report["run_id"] = run["id"]
    for field in ("prevented_attempts", "would_prevent_attempts"):
        count = run.get(field, 0)
        if type(count) is not int or count < 0:
            raise CoordinationError("Coordinator returned invalid attempt counts.")
        report[field] = count
    report["demo"] = bool(run.get("demo", False))


def _record_decision(report, decision, redactor):
    decision = _decision(decision)
    report["policy"] = redactor.clean(decision["policy"])
    report["decisions"].append(redactor.clean(decision))
    return decision


def _set_result(report, status, exit_code, reason):
    report.update(status=status, exit_code=exit_code, reason=reason)


def run(args, report, redactor):
    report.update(repository=args.repository, dependency=args.dependency, job_id=args.job_id,
                  workflow_run_id=args.workflow_run_id)
    client = Coordinator(args.server, os.environ.get("CI_GUARD_TOKEN"))
    try:
        dependencies = client.request("GET", "/api/dependencies")
        if not isinstance(dependencies, list):
            raise CoordinationError("Coordinator returned an invalid dependency list.")
        dependency = next((item for item in dependencies if isinstance(item, dict) and item.get("id") == args.dependency), None)
        if not dependency:
            raise ConfigurationError(f"Dependency {args.dependency!r} is not registered. Configure it in CI Guard first.")
        patterns = dependency.get("error_patterns")
        if not isinstance(patterns, list) or len(patterns) > 100 or any(not isinstance(p, str) or not p or len(p) > 4096 for p in patterns):
            raise ConfigurationError("Registered dependency error patterns must be bounded, nonempty literal strings.")
        created = client.request("POST", "/api/runs", {"repository": args.repository, "dependency": args.dependency,
                                "job_id": args.job_id, "workflow_run_id": args.workflow_run_id,
                                "command": shlex.join([redactor.text(argument) for argument in args.command])[:2000],
                                "idempotency_key": str(uuid.uuid4())})
        _update_run(report, created)
        decision = _record_decision(report, created.get("decision"), redactor)
        run_path = f"/api/runs/{quote(report['run_id'], safe='')}"
        probe_path = f"/api/dependencies/{quote(args.dependency, safe='')}/probe"
        if decision["action"] == "probe":
            probe = client.request("POST", probe_path, {"run_id": report["run_id"],
                                   "probe_token": decision.get("probe_token"), "probe_generation": decision.get("probe_generation")})
            if not isinstance(probe, dict):
                raise CoordinationError("Coordinator returned an invalid recovery probe response.")
            decision = _record_decision(report, probe.get("decision"), redactor)
        if decision["action"] == "block":
            _set_result(report, "blocked", 2, decision.get("reason", "Execution blocked by a confirmed dependency outage."))
        elif decision["action"] != "run":
            raise CoordinationError("Initial decision did not permit command execution.")
        else:
            while True:
                attempt = redactor.clean(execute(args.command, args.timeout, patterns))
                attempt.update(attempt_number=len(report["attempts"]) + 1, health_status="unknown", health_detail=None)
                report["attempts"].append(attempt)
                report["actual_execution_seconds"] = round(sum(a["duration_seconds"] for a in report["attempts"]), 6)
                classification = attempt["classification"]
                if classification == "passed":
                    _set_result(report, "passed", 0, "Pytest verified successful test calls." if attempt.get("verification") == "pytest"
                                else "Command exited successfully; individual test outcomes were not verified.")
                else:
                    _set_result(report, "failed", 124 if classification == "timeout" else 1,
                                f"Test command failed ({classification}).")
                policy = decision["policy"]
                probe_failed = False
                if classification == "dependency" and policy["mode"] != "off" and not policy.get("error"):
                    try:
                        probe = client.request("POST", probe_path, {"run_id": report["run_id"]})
                        if not isinstance(probe, dict) or probe.get("status") not in {"healthy", "unhealthy", "unknown"}:
                            raise CoordinationError("Coordinator returned an invalid health probe response.")
                        attempt.update(health_status=probe["status"], health_detail=redactor.text(str(probe.get("detail", "")))[:1000])
                    except CoordinationError as exc:
                        # Record actual execution even if health confirmation failed.
                        # Unknown health never authorizes another expensive attempt.
                        probe_failed = True
                        report["coordination_error"] = str(exc)
                        attempt["health_detail"] = str(exc)
                        report["reason"] += " Dependency health could not be confirmed; retries disabled."
                payload = {key: attempt[key] for key in ("attempt_number", "exit_code", "duration_seconds", "classification", "evidence", "health_status", "health_detail")}
                payload["idempotency_key"] = f"{report['run_id']}:attempt:{attempt['attempt_number']}"
                response = client.request("POST", f"{run_path}/attempts", payload)
                if not isinstance(response, dict):
                    raise CoordinationError("Coordinator returned an invalid attempt response.")
                _update_run(report, response.get("run"))
                decision = _record_decision(report, response.get("decision"), redactor)
                if probe_failed:
                    break
                if classification == "passed" or classification in {"assertion", "unknown", "timeout"}:
                    break
                policy = decision["policy"]
                if policy["mode"] == "off" or policy.get("error") or attempt["health_status"] != "unhealthy":
                    break
                if decision["action"] == "block":
                    _set_result(report, "blocked", 2, decision.get("reason", "No retry permitted during this dependency outage."))
                    break
                if decision["action"] != "retry":
                    break
                if attempt["attempt_number"] > policy["max_job_retries"]:
                    report["reason"] = "Per-job retry allowance exhausted."
                    break
        _update_run(report, client.request("POST", f"{run_path}/finish", {"status": report["status"], "reason": report["reason"]}))
    except Cancelled as exc:
        if exc.attempt:
            attempt = redactor.clean(exc.attempt)
            attempt.update(attempt_number=len(report["attempts"]) + 1, health_status="unknown",
                           health_detail=None, submitted=False)
            report["attempts"].append(attempt)
            report["actual_execution_seconds"] = round(sum(a["duration_seconds"] for a in report["attempts"]), 6)
        _set_result(report, "cancelled", 128 + exc.signum, f"Runner cancelled by signal {exc.signum}; child process group terminated.")
        _finish_best_effort(client, report)
    except (ConfigurationError, ValueError, OSError) as exc:
        _set_result(report, "error", 3, f"Configuration error: {redactor.text(str(exc))}")
        _finish_best_effort(client, report)
    except CoordinationError as exc:
        report["coordination_error"] = str(exc)
        if report["exit_code"] == 0 or not report["attempts"]:
            _set_result(report, "error", 3, str(exc))
        else:
            report["reason"] += " Coordination unavailable; no further retry was attempted."
        _finish_best_effort(client, report)
    finally:
        client.close()


def _finish_best_effort(client, report):
    if report["run_id"]:
        try:
            path = f"/api/runs/{quote(report['run_id'], safe='')}/finish"
            client.request("POST", path, {"status": report["status"], "reason": report["reason"]})
        except CoordinationError:
            pass


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    report, redactor = _initial_report(), Redactor()
    report_path = "ci-guard-report.json"
    # Preserve the requested report location even when argument validation fails.
    for index, argument in enumerate(argv):
        if argument == "--":
            break
        if argument == "--report" and index + 1 < len(argv):
            report_path = argv[index + 1]
        elif argument.startswith("--report="):
            report_path = argument.partition("=")[2]
    previous_handlers = {}
    write_report = True

    def on_signal(signum, frame):
        # A second cancellation signal must not interrupt cleanup/report writing.
        for watched in previous_handlers:
            signal.signal(watched, signal.SIG_IGN)
        raise Cancelled(signum)

    try:
        args = parse_args(argv)
        report_path = args.report
        for watched in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[watched] = signal.signal(watched, on_signal)
        run(args, report, redactor)
    except SystemExit:
        # argparse's own --help exits before executing; a child --help is ordinary argv.
        write_report = False
        raise
    except (ConfigurationError, ValueError, OSError) as exc:
        _set_result(report, "error", 3, f"Configuration error: {redactor.text(str(exc))}")
    except Cancelled as exc:
        _set_result(report, "cancelled", 128 + exc.signum, "Runner cancelled; no success was reported.")
    except CoordinationError as exc:
        _set_result(report, "error", 3, str(exc))
        report["coordination_error"] = str(exc)
    except Exception as exc:
        # A malformed external response still produces a truthful failure report.
        _set_result(report, "error", 3, f"Runner error ({type(exc).__name__}); no success was reported.")
    finally:
        if write_report:
            for error in write_reports(report, report_path, redactor):
                print(f"CI Guard: {error}", file=sys.stderr)
        for watched, previous in previous_handlers.items():
            signal.signal(watched, previous)
    print(f"CI Guard: {report['status']} (exit {report['exit_code']}); report: {redactor.text(report_path)}")
    return report["exit_code"]
