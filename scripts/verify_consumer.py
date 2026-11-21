"""Run the pinned StreamPulse unittest suite through the real CI Guard client.

The consumer's local HTTP fixtures are healthy. This is separate evidence from
the controllable inventory outage exercised by verify_demo.py.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit

import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runner"))
from ci_guard_runner.report import Redactor

REPOSITORY = "ayushmanthehero-lab/StreamPulse"
COMMIT = "5af46b946bdf453b8cf2e978d3310f51abc7e2f9"
EXPECTED_TESTS = 34
DEPENDENCY = "streampulse-demo-healthcheck"
NO_RETRY_SIGNATURE = "CI_GUARD_STREAMPULSE_FIXTURE_NO_RETRY_EVIDENCE"


class VerificationError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise VerificationError(message)


def parse_unittest_result(output: str, *, expected: int = EXPECTED_TESTS) -> dict:
    match = re.search(r"^Ran (\d+) tests? in ([0-9]+(?:\.[0-9]+)?)s\s*\n\s*OK(?: \(([^\n]*)\))?\s*\Z", output, re.MULTILINE)
    require(match is not None, "The actual unittest output does not end with a successful test-count summary.")
    count, seconds = int(match[1]), float(match[2])
    qualifiers = {}
    for qualifier in (match[3] or "").split(", "):
        if qualifier:
            detail = re.fullmatch(r"(skipped|expected failures)=(\d+)", qualifier)
            require(detail is not None, "The unittest result contains an unsupported outcome qualifier.")
            qualifiers[detail[1]] = int(detail[2])
    skipped = qualifiers.get("skipped", 0)
    expected_failures = qualifiers.get("expected failures", 0)
    passed = count - skipped - expected_failures
    require(count > 0 and passed > 0, "No successfully executed unittest cases were verified; zero-test and all-skipped suites cannot pass.")
    require(count == expected, f"The pinned consumer must execute {expected} tests; the actual output reported {count}.")
    require(passed >= 0, "The unittest outcome counts are inconsistent.")
    return {"tests_run": count, "passed": passed, "skipped": skipped,
            "expected_failures": expected_failures, "execution_seconds": seconds,
            "verification": "actual_unittest_terminal_summary"}


def controlled_comparison(evidence: dict) -> dict:
    runs = evidence.get("runs", [])
    require(isinstance(runs, list), "Controlled-demo evidence has no run list.")
    observed = next((run for run in runs if run.get("scenario") == "outage" and run.get("mode") == "observe"), None)
    enforced = next((run for run in runs if run.get("scenario") == "outage" and run.get("mode") == "enforce"), None)
    require(observed is not None and enforced is not None, "Both measured observe and enforce outage batches are required.")

    def count(run, expected_status):
        jobs = run.get("jobs", [])
        require(len(jobs) == 4, "The controlled comparison requires four concurrent jobs in each batch.")
        require(all(job.get("status") == expected_status and type(job.get("attempts")) is int and 0 <= job["attempts"] <= 3 for job in jobs),
                "Controlled job outcomes or execution counts are invalid.")
        total = sum(job["attempts"] for job in jobs)
        require(total == run.get("attempts"), "Controlled aggregate counts differ from recorded job execution counts.")
        return total

    observe_count, enforce_count = count(observed, "failed"), count(enforced, "blocked")
    require(observe_count == 12 and 1 <= enforce_count <= 6, "The measured controlled result must be 12 observe attempts versus at most 6 enforce attempts.")
    return {"concurrent_jobs": 4, "observe_attempts": observe_count, "enforce_attempts": enforce_count,
            "shared_retry_budget": 2, "scope": "controlled_inventory_fixture"}


def load_runner_credentials():
    path = ROOT / ".local/runtime.env"
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                if key in {"CI_GUARD_TOKEN", "CI_GUARD_ADMIN_TOKEN"}:
                    os.environ.setdefault(key, value)


def run_url() -> str | None:
    repository, run_id = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_RUN_ID")
    if repository and run_id:
        return f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com').rstrip('/')}/{repository}/actions/runs/{run_id}"
    return None


def inspect_consumer(path: Path, python: Path) -> dict:
    actual = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], check=True,
                            capture_output=True, text=True, timeout=10).stdout.strip()
    require(actual == COMMIT, "The consumer checkout does not match the reviewed commit pin.")
    clean = subprocess.run(["git", "-C", str(path), "diff", "--quiet", "HEAD"], timeout=10)
    require(clean.returncode == 0, "The pinned consumer has tracked modifications; verification requires unchanged source.")
    code = ("import importlib.metadata,json,sys,unittest; "
            "suite=unittest.defaultTestLoader.discover('workers/analysis/tests'); "
            "print(json.dumps({'python':sys.version.split()[0], 'numpy':importlib.metadata.version('numpy'), "
            "'scipy':importlib.metadata.version('scipy'), 'discovered_tests':suite.countTestCases()}))")
    result = subprocess.run([str(python), "-c", code], cwd=path, check=True,
                            capture_output=True, text=True, timeout=30)
    metadata = json.loads(result.stdout)
    require(metadata["python"].startswith("3.13."), "The pinned consumer must run in its Python 3.13 environment.")
    require(metadata["numpy"] == "2.4.4" and metadata["scipy"] == "1.17.1", "Consumer NumPy/SciPy versions differ from the reviewed pins.")
    require(metadata["discovered_tests"] == EXPECTED_TESTS, "Discovery did not find the expected 34 consumer unittest cases.")
    return metadata


def verify(args, evidence):
    consumer = args.consumer_path.resolve()
    # Resolving a venv's interpreter symlink would escape its installed packages.
    python = args.consumer_python.absolute()
    report_path = args.report.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.unlink(missing_ok=True)
    evidence["environment"] = inspect_consumer(consumer, python)
    evidence["controlled_outage"] = controlled_comparison(json.loads(args.controlled_report.read_text()))

    parsed = urlsplit(args.inventory)
    require(parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"} and not parsed.username and not parsed.password,
            "The demonstration healthcheck must use an explicit localhost HTTP fixture.")
    load_runner_credentials()
    token = os.environ.get("CI_GUARD_ADMIN_TOKEN") or os.environ.get("CI_GUARD_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(base_url=args.server, headers=headers, timeout=15, trust_env=False) as client:
        # This is a healthy local admission fixture, not a production StreamPulse dependency.
        with httpx.Client(timeout=5, trust_env=False) as fixture:
            fixture.post(args.inventory.rstrip("/") + "/__control", json={"healthy": True}).raise_for_status()
        configuration = {"id": DEPENDENCY, "name": "StreamPulse demo healthcheck (healthy local fixture)",
                         "health_url": args.inventory.rstrip("/") + "/health",
                         "error_patterns": [NO_RETRY_SIGNATURE], "timeout_seconds": 3}
        dependencies = client.get("/api/dependencies").raise_for_status().json()
        existing = next((dependency for dependency in dependencies if dependency["id"] == DEPENDENCY), None)
        if existing:
            require(existing["health_url"] == configuration["health_url"] and existing["error_patterns"] == [NO_RETRY_SIGNATURE],
                    "The existing consumer demonstration healthcheck has different configuration.")
        else:
            client.post("/api/dependencies", json=configuration).raise_for_status()
        health = client.post(f"/api/dependencies/{DEPENDENCY}/probe", json={}).raise_for_status().json()
        require(health["status"] == "healthy", "The local consumer admission fixture is not healthy.")
        evidence["dependency"] = {"id": DEPENDENCY, "status": health["status"],
                                  "scope": "healthy_local_demo_admission_fixture",
                                  "consumer_http_fixtures": "Started locally by the unchanged StreamPulse unittest suite; no production service is contacted."}
        command = [str(python), "-m", "unittest", "discover", "-s", "workers/analysis/tests", "-v"]
        wrapper = [sys.executable, "-m", "ci_guard_runner", "run", "--server", args.server,
                   "--repository", REPOSITORY, "--dependency", DEPENDENCY, "--job-id", "streampulse-analysis-unittest",
                   "--report", str(report_path), "--timeout", "120", "--", *command]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT / "runner")
        env.pop("GITHUB_STEP_SUMMARY", None)
        env.pop("GITHUB_OUTPUT", None)
        process = subprocess.Popen(wrapper, cwd=consumer, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            process.communicate(timeout=180)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            raise VerificationError("The guarded consumer run exceeded its outer time limit.")
        require(report_path.exists(), "The actual CI Guard consumer command did not produce a report.")
        report = json.loads(report_path.read_text())
        evidence.update(run_id=report.get("run_id"), wrapper_exit_code=process.returncode,
                        ci_guard_report=str(args.report), command=command)
        require(process.returncode == 0 and report["status"] == "passed" and not report.get("coordination_error"),
                "The actual guarded consumer command failed or could not coordinate its result.")
        require(report["repository"] == REPOSITORY and report["dependency"] == DEPENDENCY,
                "The recorded consumer run has an incorrect project or dependency identity.")
        attempts = report["attempts"]
        require(len(attempts) == 1 and attempts[0]["classification"] == "passed", "The healthy consumer suite must execute exactly once.")
        require(attempts[0].get("verification") == "command_exit", "The unittest consumer must retain the runner's explicit generic-command verification label.")
        require(NO_RETRY_SIGNATURE not in attempts[0]["stdout"] + attempts[0]["stderr"], "Unexpected dependency evidence appeared in the consumer output.")
        evidence["unittest"] = parse_unittest_result(attempts[0]["stderr"])
        evidence["runner_verification"] = "command_exit"
        evidence["actual_execution_seconds"] = report["actual_execution_seconds"]
        stored = client.get(f"/api/runs/{report['run_id']}").raise_for_status().json()
        require(stored["status"] == "passed" and stored["attempt_count"] == 1 and stored["repository"] == REPOSITORY,
                "The coordinator did not persist the actual successful consumer attempt.")
        evidence["status"] = "passed"


def write_summary(evidence):
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if not destination:
        return
    lines = ["## CI Guard: measured demonstration", "", f"Verification: **{evidence['status']}**", ""]
    if evidence.get("workflow_run_url"):
        lines += [f"[Actual GitHub workflow run]({evidence['workflow_run_url']})", ""]
    else:
        lines += ["Local verification only; no GitHub-hosted run is claimed.", ""]
    comparison = evidence.get("controlled_outage")
    if comparison:
        lines += ["### Controlled fixture outage", "",
                  f"Four concurrent jobs executed **{comparison['observe_attempts']} attempts in observe** and "
                  f"**{comparison['enforce_attempts']} attempts in enforce** with a shared retry budget of two (maximum six attempts).",
                  "These are measured executions against the controlled inventory fixture, separate from the consumer repository below.", ""]
    lines += ["### Real consumer suite: healthy local fixtures", "",
              f"Repository: [{REPOSITORY}](https://github.com/{REPOSITORY})  ",
              f"Pinned revision: [`{COMMIT}`](https://github.com/{REPOSITORY}/tree/{COMMIT})", ""]
    if counts := evidence.get("unittest"):
        lines += [f"**{counts['tests_run']} unittest cases executed; {counts['passed']} passed; {counts['skipped']} skipped.**",
                  "The unchanged suite ran once through CI Guard using Python 3.13, NumPy 2.4.4, and SciPy 1.17.1.",
                  "The runner records generic command-exit verification; this additional verifier checks the real unittest terminal count and rejects empty or all-skipped success.",
                  "The suite starts its own localhost HTTP fixtures. No StreamPulse production outage or avoided retries are claimed.", ""]
    if evidence.get("error"):
        lines += [f"Verification failed: {evidence['error']}", ""]
    lines += ["Artifacts contain the measured controlled batches, consumer verification, and its actual CI Guard JSON report.", ""]
    with open(destination, "a", encoding="utf-8") as output:
        output.write("\n".join(lines))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default="http://127.0.0.1:8080")
    parser.add_argument("--inventory", default="http://127.0.0.1:8090")
    parser.add_argument("--consumer-path", type=Path, default=ROOT / ".local/consumer")
    parser.add_argument("--consumer-python", type=Path, default=ROOT / ".local/consumer-venv/bin/python")
    parser.add_argument("--controlled-report", type=Path, default=ROOT / ".local/verification-demo.json")
    parser.add_argument("--report", type=Path, default=ROOT / ".local/ci-guard-report-consumer.json")
    parser.add_argument("--output", type=Path, default=ROOT / ".local/verification-consumer.json")
    args = parser.parse_args(argv)
    evidence = {"schema_version": 1, "status": "failed", "repository": REPOSITORY, "commit": COMMIT,
                "expected_tests": EXPECTED_TESTS, "workflow_run_url": run_url(),
                "scope": "real_pinned_consumer_healthy_tests", "verified_at": datetime.now(timezone.utc).isoformat()}
    try:
        verify(args, evidence)
    except Exception as exc:
        evidence["error"] = str(exc) if isinstance(exc, VerificationError) else f"Verification could not complete ({type(exc).__name__})."
    evidence = Redactor().clean(evidence)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, indent=2) + "\n")
    write_summary(evidence)
    if evidence["status"] == "passed":
        print(f"Verified {REPOSITORY}@{COMMIT}: {evidence['unittest']['tests_run']} actual unittest cases, one recorded CI Guard attempt.")
        return 0
    print(f"Consumer verification failed: {evidence['error']}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
