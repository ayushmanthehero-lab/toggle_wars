"""Exercise the actual terminal and CI Guard processes in the disposable lab."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

import httpx

if __package__:
    from .flagsmith_experiment import measure_batch
else:
    from flagsmith_experiment import measure_batch

ROOT = Path(__file__).resolve().parents[1]


def compare_outage(staging_reports, development_reports):
    staging = measure_batch(staging_reports, mode="enforce", scenario="outage")
    development = measure_batch(development_reports, mode="observe", scenario="outage")
    if staging >= development:
        raise ValueError("Enforce must execute fewer tests in the controlled outage.")
    return staging, development


def outage(port):
    response = httpx.post(f"http://127.0.0.1:{port}/api/demo/run",
                         json={"scenario": "outage", "jobs": 4},
                         headers={"Authorization": f"Bearer {os.environ['CI_GUARD_ADMIN_TOKEN']}"},
                         timeout=100, trust_env=False)
    if response.status_code != 200:
        raise RuntimeError(f"CI Guard demo returned HTTP {response.status_code}.")
    return [item["report"] for item in response.json()["results"]]


def verify(config, development_id, staging_id, production_id):
    terminal = [sys.executable, str(ROOT / "scripts/flagsmith_terminal.py"), "--config", str(config),
                "--write-env", str(staging_id), "--write-env", str(production_id)]
    commands = [
        f"set {staging_id} show_retry_summary on", f"apply {staging_id}",
        f"set {staging_id} ci_guard_mode enforce", f"apply {staging_id}",
        f"set {production_id} ci_guard_mode enforce", "refresh", "quit",
    ]
    result = subprocess.run(terminal, input="\n".join(commands) + "\n", text=True,
                            capture_output=True, timeout=45, cwd=ROOT)
    print(result.stdout)
    if (result.returncode != 0 or result.stdout.count("Saved and verified in Flagsmith:") != 2
            or "That environment is read-only." not in result.stderr):
        raise RuntimeError("Terminal did not verify the two Staging edits and Production refusal.")
    from flagsmith_terminal import FlagsmithTerminal
    with_api = FlagsmithTerminal(os.environ["FLAGSMITH_API_URL"], os.environ["FLAGSMITH_ADMIN_API_KEY"],
                                int(os.environ["FLAGSMITH_PROJECT_ID"]), {staging_id})
    try:
        environments, matrix = with_api.snapshot()
        if {e.id for e in environments} != {development_id, staging_id, production_id}:
            raise RuntimeError("Terminal did not discover the lab's full set of environments.")
        for eid in (development_id, staging_id, production_id):
            if (matrix[eid]["show_retry_summary"].enabled != (eid == staging_id)
                    or matrix[eid]["ci_guard_mode"].value != ("enforce" if eid == staging_id else "observe")):
                raise RuntimeError("Only Staging should have changed in Flagsmith.")
    finally:
        with_api.close()
    staging, development = compare_outage(outage(18181), outage(18180))
    print(f"Verified: Development remained Observe ({development} real attempts); "
          f"Staging ran Enforce ({staging} real attempts). Production stayed unchanged. No jobs passed.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    fields = ["DEVELOPMENT", "STAGING", "PRODUCTION"]
    for field in fields:
        parser.add_argument(field.lower(), type=int)
    args = parser.parse_args()
    verify(args.config, args.development, args.staging, args.production)


if __name__ == "__main__":
    main()
