"""Start the Docker-only CI Guard terminal lab with real local Flagsmith."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from tempfile import TemporaryDirectory
import time

import httpx

if __package__:
    from . import ci_guard_onboard as onboard, flagsmith_terminal as terminal
    from .flagsmith_terminal_demo import outage, verify
else:
    import ci_guard_onboard as onboard
    import flagsmith_terminal as terminal
    from flagsmith_terminal_demo import outage, verify

from ci_guard.db import Database, dump
from ci_guard.github import GitHubStore, iso

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path("/bootstrap/terminal.env")
DEVELOPMENT_PORT, STAGING_PORT, INVENTORY_PORT = 18180, 18181, 18190
REPO, DEPENDENCY = "demo/staging-app", "onboarding-inventory"


class LabError(ValueError):
    pass


def lab_config(config):
    values = dict(line.split("=", 1) for line in config.read_text(encoding="utf-8").splitlines())
    if values.get("FLAGSMITH_API_URL") not in ("http://127.0.0.1:8000/api/v1/", "http://127.0.0.1:18000/api/v1/"):
        raise LabError("Unexpected origin in the disposable Flagsmith config.")
    ids = ("FLAGSMITH_PROJECT_ID", "TERMINAL_DEVELOPMENT_ID", "TERMINAL_STAGING_ID", "TERMINAL_PRODUCTION_ID")
    keys = ("FLAGSMITH_ADMIN_API_KEY", "TERMINAL_DEVELOPMENT_KEY", "TERMINAL_STAGING_KEY")
    if any(not re.fullmatch(r"[1-9][0-9]{0,11}", values.get(key, "")) for key in ids) or any(
            not re.fullmatch(r"[A-Za-z0-9_.-]{1,255}", values.get(key, "")) for key in keys):
        raise LabError("Invalid disposable Flagsmith config. Reset the quick-start lab.")
    return {key: values[key] for key in ("FLAGSMITH_API_URL", *ids, *keys)}


def stop(child):
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=4)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=4)


def start(stack, directory, label, args, env):
    log = stack.enter_context((directory / (label + ".log")).open("wb"))
    child = subprocess.Popen([sys.executable, *args], cwd=ROOT, env=env, stdout=log, stderr=log)
    stack.callback(stop, child)
    return child


def await_services(processes):
    paths = [(INVENTORY_PORT, "/health"), (DEVELOPMENT_PORT, "/api/ready"), (STAGING_PORT, "/api/ready")]
    with httpx.Client(timeout=2, trust_env=False) as client:
        for _ in range(60):
            if any(child.poll() is not None for child in processes):
                break
            try:
                if all(client.get(f"http://127.0.0.1:{port}{path}").status_code == 200 for port, path in paths):
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
    raise LabError("A local coordinator or inventory service did not become ready. Reset the quick-start lab and retry.")


def outage_summary(reports):
    if not reports or any(not isinstance(report, dict) or not isinstance(report.get("attempts"), list)
                          or report.get("status") not in ("blocked", "failed", "passed") for report in reports):
        raise LabError("Outage evidence was incomplete.")
    return sum(len(report["attempts"]) for report in reports), sum(report["status"] == "passed" for report in reports)


def run_outage():
    print("Running four actual pytest jobs per environment against the same lab outage…", flush=True)
    for port, name in ((DEVELOPMENT_PORT, "Development"), (STAGING_PORT, "Staging")):
        try:
            reports = outage(port)
            count, passed = outage_summary(reports)
            modes = {report.get("policy", {}).get("mode") for report in reports}
        except (httpx.HTTPError, RuntimeError, TypeError, ValueError, AttributeError):
            raise LabError("Outage check could not collect real CI Guard reports.") from None
        mode = next(iter(modes)) if len(modes) == 1 else None
        if mode not in ("observe", "enforce", "off"):
            raise LabError("Outage check did not have a consistent Flagsmith policy.")
        print(f"{name:12} {mode.title():8} {count:2} actual attempts | {passed}/{len(reports)} jobs passed")


def setup_example(cfg, staging_db):
    """Walk through doctor using an offline fixture, never a real GitHub account."""
    url = f"http://127.0.0.1:{STAGING_PORT}"
    with httpx.Client(base_url=url, timeout=5, trust_env=False,
                      headers={"Authorization": "Bearer " + os.environ["CI_GUARD_ADMIN_TOKEN"]}) as client:
        try:
            deps = client.get("/api/dependencies")
            deps.raise_for_status()
            if not any(d["id"] == DEPENDENCY for d in deps.json()):
                client.post("/api/dependencies", json={"id": DEPENDENCY, "name": "Lab inventory",
                            "health_url": f"http://127.0.0.1:{INVENTORY_PORT}/health",
                            "error_patterns": ["INVENTORY_DEPENDENCY_UNAVAILABLE"]}).raise_for_status()
        except (httpx.HTTPError, ValueError, TypeError, KeyError):
            raise LabError("Could not register the local fixture dependency.") from None
    db = Database(staging_db)
    with db.transaction() as con:
        con.execute("INSERT OR IGNORE INTO github_connections VALUES (?,?,?,?,?)",
                    ("quickstart", dump({"id": "quickstart", "created_at": iso()}), None, "environment", None))
        con.execute("INSERT OR IGNORE INTO github_projects VALUES (?,?,?,?)",
                    ("quickstart", "quickstart", 1, dump({"id": "quickstart", "full_name": REPO})))
    project_token = GitHubStore(db).rotate_runner_token("quickstart", [DEPENDENCY])["token"]
    try:
        mode = onboard.doctor(flagsmith_url=cfg["FLAGSMITH_API_URL"], flagsmith_token=cfg["FLAGSMITH_ADMIN_API_KEY"],
                              scheme="Api-Key", project=int(cfg["FLAGSMITH_PROJECT_ID"]),
                              environment=int(cfg["TERMINAL_STAGING_ID"]), coordinator_url=url,
                              project_token=project_token, repository=REPO, dependency=DEPENDENCY)
    except (onboard.SetupError, terminal.TerminalError) as exc:
        raise LabError(str(exc)) from None
    generated = Path(staging_db).parent / "ci-guard-starter.yml"
    generated.write_text(onboard.starter(), encoding="utf-8")
    print("Onboarding example: local fixture repository only; GitHub was not contacted.")
    print("  PASS  Selected Staging and read its Flagsmith policy")
    print(f"  PASS  Scoped token can see {DEPENDENCY} for {REPO}")
    print(f"  PASS  Coordinator matches the effective {mode.title()} policy")
    print(f"  READY  {generated.name} (reviewable starter; nothing was pushed)")
    print("    isolated-pr: pull requests, local fixture, no CI Guard secret")
    print("    guarded-staging: manual dispatch, protected branch, staging secret")
    print("Run the onboard service for your own repo; see docs/QUICKSTART.md.")


def run(config, *, check=False, report_root=None):
    cfg = lab_config(config)
    # Drop ambient runtime credentials; every service is bound to lab loopback.
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("FLAGSMITH_", "CI_GUARD_", "TERMINAL_")) and not k.lower().endswith("_proxy")}
    env.update(CI_GUARD_DEMO_MODE="true", CI_GUARD_DEMO_INVENTORY_URL=f"http://127.0.0.1:{INVENTORY_PORT}",
               CI_GUARD_ADMIN_TOKEN=secrets.token_urlsafe(24), NO_PROXY="localhost,127.0.0.1",
               no_proxy="localhost,127.0.0.1", FLAGSMITH_API_URL=cfg["FLAGSMITH_API_URL"])
    env["CI_GUARD_TOKEN"] = env["CI_GUARD_ADMIN_TOKEN"]
    # The subprocesses only get the SDK key for their own environment.
    os.environ.clear()
    os.environ.update(env, **{k: v for k, v in cfg.items() if k not in env})
    with ExitStack() as stack:
        directory = Path(stack.enter_context(TemporaryDirectory(prefix="ci-guard-lab-", dir=report_root)))
        processes = [start(stack, directory, "inventory", ["examples/inventory_service.py", "--port", str(INVENTORY_PORT)], env)]
        for port, name in ((DEVELOPMENT_PORT, "DEVELOPMENT"), (STAGING_PORT, "STAGING")):
            processes.append(start(stack, directory, name, ["scripts/serve.py", "--port", str(port)],
                                   dict(env, FLAGSMITH_ENVIRONMENT_KEY=cfg[f"TERMINAL_{name}_KEY"],
                                        CI_GUARD_DATABASE_PATH=str(directory / f"{port}.sqlite"))))
        await_services(processes)
        dev, staging, prod = (int(cfg[f"TERMINAL_{name}_ID"]) for name in ("DEVELOPMENT", "STAGING", "PRODUCTION"))
        if check:
            verify(config, dev, staging, prod)
            setup_example(cfg, directory / f"{STAGING_PORT}.sqlite")
            return 0
        print("CI Guard Quick Start · private self-hosted Flagsmith lab")
        print(f"Try: set {staging} ci_guard_mode enforce  |  then: apply {staging}  |  then: outage")
        return terminal.main(["--config", str(config), "--write-env", str(staging)], extra_commands={
            "outage": run_outage, "setup-example": lambda: setup_example(cfg, directory / f"{STAGING_PORT}.sqlite"),
        })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="Verify edits, real runs and the onboarding example on a fresh lab.")
    args = parser.parse_args()
    try:
        return run(CONFIG, check=args.verify)
    except (LabError, RuntimeError, OSError, ValueError) as exc:
        print(f"Lab stopped: {exc}" if isinstance(exc, LabError) else "Lab stopped: seed or verification failed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
