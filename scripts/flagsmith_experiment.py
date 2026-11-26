"""Run a real Flagsmith experiment against a disposable loopback CI Guard stack."""

import argparse
import json
import math
import os
import secrets
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from flagsmith import EventProcessorConfig, Flagsmith

ROOT = Path(__file__).resolve().parents[1]
FLAG = "ci_guard_mode"
EVENT = "ci_guard_outage_batch_executions"
MIN_PER_ARM = 50
MODES = {"control": "observe", "enforce": "enforce"}


def measure_batch(runs, *, mode, scenario):
    """Count executed commands only after verifying real, recorded outcomes."""
    if len(runs) != (4 if scenario == "outage" else 1):
        raise ValueError("Unexpected batch size")
    statuses, classification = {
        "outage": ({"failed", "blocked"}, "dependency"),
        "healthy": ({"passed"}, "passed"),
        "assertion": ({"failed"}, "assertion"),
    }[scenario]
    measured = 0
    for report in runs:
        policy = report["policy"]
        if (report["status"] not in statuses or report.get("coordination_error")
                or policy.get("error") or policy["source"] != "flagsmith" or policy["mode"] != mode):
            raise ValueError("Invalid outcome or incorrect Flagsmith assignment")
        attempts = report["attempts"]
        if any(a["classification"] != classification for a in attempts):
            raise ValueError("Unexpected test classification")
        if not attempts and report["status"] != "blocked":
            raise ValueError("Only blocked jobs may have zero attempts")
        measured += len(attempts)
    if scenario != "outage" and measured != 1:
        raise ValueError("Healthy checks and assertion failures must each execute once")
    if measured == 0:
        raise ValueError("A fresh outage batch must have executed tests")
    return measured


def split_check(provider_p, expected):
    if provider_p is not None:
        return provider_p
    # Current self-hosted Flagsmith may omit SRM for a segment-based split.
    # Use a separately labelled chi-squared check for this known 50/50 design.
    control, enforce = (expected[key][0] for key in MODES)
    if control + enforce == 0:
        return 0.0
    return math.erfc(abs(control - enforce) / math.sqrt(2 * (control + enforce)))


def select_winner(payload, metric_id, expected):
    if set(expected) != set(MODES) or split_check(payload.get("srm_p_value"), expected) < 0.001:
        return None
    rows = [row for row in payload.get("metrics", []) if row.get("metric_id") == metric_id]
    if len(rows) != 1 or set(rows[0]["variants"]) != set(MODES):
        return None
    row = rows[0]
    for arm, (count, total) in expected.items():
        variant = row["variants"][arm]
        if count < MIN_PER_ARM or variant["n"] != count or variant["sum"] != total:
            return None
    inference = row["inference"].get("enforce")
    if inference and inference["chance_to_win"] >= 0.95 and inference["lift"] < 0:
        return "enforce"
    if inference and inference["chance_to_win"] <= 0.05 and inference["lift"] > 0:
        return "control"
    return None


def loopback(url, *, path=""):
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path != path):
        raise ValueError("Experiment must use an explicit loopback URL and a disposable instance")
    return url


def request(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.is_error:
        # The URL includes environment credentials; never print it or the body.
        raise RuntimeError(f"{method} {path.strip('/').split('/')[-1]}: HTTP {response.status_code}")
    return response.json() if response.content else None


def configure(config, admin):
    env_key = config["FLAGSMITH_ENVIRONMENT_KEY"]
    env = f"environments/{env_key}/"
    existing = request(admin, "GET", env + "experiments/")
    if existing["results"]:
        raise RuntimeError("Use a fresh isolated Flagsmith environment")
    connection = request(admin, "POST", env + "warehouse-connections/", json={
        "name": "Flagsmith database", "warehouse_type": "postgres",
        "config": {"source": "flagsmith_database"},
    })
    if connection.get("status") == "errored":
        raise RuntimeError("Flagsmith's built-in events warehouse was not ready")
    metric = request(admin, "POST", env + "experiment-metrics/", json={
        "name": "Executed tests per outage batch", "aggregation": "mean", "direction": "down",
        "definition": {"version": 1, "event": EVENT},
    })
    experiment = request(admin, "POST", env + "experiments/", json={
        "feature": int(config["CI_GUARD_EXPERIMENT_MODE_FEATURE_ID"]),
        "name": "CI Guard: Observe versus Enforce",
        "hypothesis": "A shared retry allowance reduces actual test executions in a controlled outage.",
        "metrics": [{"metric": metric["id"], "expected_direction": "decrease"}],
        "experiment_rollout": {
            "enabled": True, "rollout_percentage": 100,
            "feature_state_value": {"type": "string", "value": "observe"},
            "multivariate_feature_state_values": [{
                "multivariate_feature_option": int(config["CI_GUARD_EXPERIMENT_ENFORCE_VARIANT_ID"]),
                "percentage_allocation": 50,
            }],
        },
    })
    path = env + f"experiments/{experiment['id']}/"
    started = request(admin, "POST", path + "start/")
    if started["status"] != "running":
        raise RuntimeError("Flagsmith did not start the experiment")
    return path, metric["id"]


def apply_winner(config, admin, path, winner, repositories):
    if winner not in MODES:
        raise ValueError("The experiment has no supported winner")
    decision = {"rollout_decision": "control"}
    if winner == "enforce":
        decision = {"rollout_decision": "variant",
                    "rollout_variant": int(config["CI_GUARD_EXPERIMENT_ENFORCE_VARIANT_ID"])}
    response = request(admin, "POST", path + "complete/", json=decision)
    if response["status"] != "completed":
        raise RuntimeError("Flagsmith did not complete the experiment")
    flags = Flagsmith(environment_key=config["FLAGSMITH_ENVIRONMENT_KEY"],
                      api_url=loopback(config["FLAGSMITH_API_URL"], path="/api/v1/"))
    for repo in [*repositories, "ci-guard-exp/unseen"]:
        updated = flags.get_identity_flags("repo:" + repo)
        if not updated.is_feature_enabled(FLAG) or updated.get_feature_value(FLAG) != MODES[winner]:
            raise RuntimeError("Winning variant was not applied to the same flag")


def run_experiment(config, output):
    flags_url = loopback(config["FLAGSMITH_API_URL"], path="/api/v1/")
    guard_url = loopback(config["CI_GUARD_EXPERIMENT_COORDINATOR_URL"])
    healthy_url = loopback(config["CI_GUARD_EXPERIMENT_HEALTHY_URL"])
    outage_url = loopback(config["CI_GUARD_EXPERIMENT_OUTAGE_URL"])
    if len({guard_url, healthy_url, outage_url}) != 3:
        raise ValueError("The coordinator and the two fixtures must be separate")
    token = config["CI_GUARD_ADMIN_TOKEN"]
    prefix = "exp-" + secrets.token_hex(4)
    counts, totals = Counter(), Counter()
    representatives, outcomes, guardrails = {}, [], {}
    sdk = Flagsmith(
        environment_key=config["FLAGSMITH_ENVIRONMENT_KEY"], api_url=flags_url,
        enable_events=True, event_processor_config=EventProcessorConfig(events_api_url=flags_url.removesuffix("v1/")),
    )
    runner_env = {k: v for k, v in os.environ.items() if k.lower() not in {
        "all_proxy", "http_proxy", "https_proxy",
    }}
    runner_env.update(CI_GUARD_TOKEN=token, PYTHONPATH=os.pathsep.join(
        [str(ROOT / "runner"), str(ROOT / "server")]), CI_GUARD_DEMO_RUN="true")
    with httpx.Client(base_url=flags_url, headers={
            "Authorization": "Api-Key " + config["FLAGSMITH_ADMIN_API_KEY"],
        }, timeout=40, trust_env=False) as admin, httpx.Client(
            base_url=guard_url, headers={"Authorization": "Bearer " + token},
            timeout=15, trust_env=False,
        ) as guard, tempfile.TemporaryDirectory(prefix="ci-guard-flagsmith-") as temp:
        if request(guard, "GET", "/api/health")["demo_mode"]:
            raise RuntimeError("This experiment requires authenticated CI Guard, not anonymous demo mode")
        if request(guard, "GET", "/api/ready")["policy_source"] != "flagsmith":
            raise RuntimeError("Coordinator is not using Flagsmith")
        path, metric_id = configure(config, admin)

        def run_batch(pool, repo, dependency, scenario, mode):
            fixture = outage_url if scenario == "outage" else healthy_url
            request(guard, "POST", "/api/dependencies", json={
                "id": dependency, "name": dependency, "health_url": fixture + "/health",
                "error_patterns": ["INVENTORY_DEPENDENCY_UNAVAILABLE"], "timeout_seconds": 3,
            })

            def job(index):
                report = Path(temp) / f"{dependency}-{index}.json"
                command = [
                    sys.executable, "-m", "ci_guard_runner", "run", "--server", guard_url,
                    "--repository", repo, "--dependency", dependency, "--job-id", f"{dependency}-{index}",
                    "--report", str(report), "--timeout", "25", "--", sys.executable, "-m",
                    "pytest", str(ROOT / "examples/test_inventory.py"), "-q",
                ]
                completed = subprocess.run(command, env={
                    **runner_env, "INVENTORY_URL": fixture,
                    "DEMO_ASSERTION_FAILURE": str(scenario == "assertion").lower(),
                }, cwd=ROOT, capture_output=True, timeout=80, check=False)
                if not report.is_file():
                    raise ValueError("CI Guard did not produce a report")
                value = json.loads(report.read_text())
                if (completed.returncode == 0) != (value["status"] == "passed"):
                    raise ValueError("Run status did not match its exit code")
                stored = request(guard, "GET", "/api/runs/" + value["run_id"])
                if stored["attempt_count"] != len(value["attempts"]) or stored["status"] != value["status"]:
                    raise ValueError("Coordinator and local runner evidence disagree")
                return value

            runs = list(pool.map(job, range(4 if scenario == "outage" else 1)))
            return {
                "executions": measure_batch(runs, mode=mode, scenario=scenario),
                "statuses": [run["status"] for run in runs],
                "run_ids": [run["run_id"] for run in runs],
            }

        def traffic(item):
            repo = f"ci-guard-exp/{prefix}-{item:03}"
            identity = "repo:" + repo
            traits = {"repository": repo, "team": "ci-guard-exp"}
            flag = sdk.get_experiment_flag(FLAG, identity, traits)
            if not flag.enabled or not flag.experiment or not flag.experiment.in_experiment:
                raise ValueError("Flagsmith did not assign experiment traffic")
            variant = flag.variant
            if variant not in MODES or flag.value != MODES[variant]:
                raise ValueError("Unexpected Flagsmith variation")
            with ThreadPoolExecutor(max_workers=4) as jobs:
                evidence = run_batch(jobs, repo, f"{prefix}-{item:03}", "outage", flag.value)
            return variant, repo, identity, evidence

        with ThreadPoolExecutor(max_workers=4) as batches:
            for idx, (variant, repo, identity, evidence) in enumerate(batches.map(traffic, range(144)), 1):
                counts[variant] += 1
                totals[variant] += evidence["executions"]
                representatives.setdefault(variant, repo)
                sdk.track_event(EVENT, identifier=identity, value=evidence["executions"])
                outcomes.append({"variant": variant, **evidence})
                if idx % 24 == 0:
                    print(f"Recorded {idx}/144 real outage batches.", flush=True)

        with ThreadPoolExecutor(max_workers=2) as jobs:
            for variant, repo in representatives.items():
                for scenario in ("healthy", "assertion"):
                    label = f"{variant}-{scenario}"
                    guardrails[label] = run_batch(jobs, repo, f"{prefix}-{label}", scenario, MODES[variant])
        sdk.flush_events()
        sdk.close()
        request(admin, "POST", path + "results/refresh/")
        result = None
        for _ in range(40):
            result = request(admin, "GET", path + "results/")["results"]
            if result and result.get("payload"):
                break
            time.sleep(1)
        aggregate = {key: (counts[key], totals[key]) for key in MODES}
        payload = (result or {}).get("payload") or {}
        winner = select_winner(payload, metric_id, aggregate) if len(guardrails) == 4 else None
        evidence = {
            "source": "Flagsmith Experimentation and real CI Guard subprocesses",
            "traffic": "144 synthetic repository identities; four real pytest jobs per outage batch",
            "config": {"enrollment_percent": 100, "observe_percent": 50, "enforce_percent": 50},
            "cohorts": {MODES[key]: {"batches": n, "attempts": t, "mean": t / n if n else None}
                        for key, (n, t) in aggregate.items()},
            "flagsmith_results": payload,
            "sample_ratio_p": split_check(payload.get("srm_p_value"), aggregate),
            "sample_ratio_source": "flagsmith" if payload.get("srm_p_value") is not None else "local_50_50_check",
            "guardrails": guardrails,
            "outcomes": outcomes,
            "winner": winner,
            "rollout_verified_in_lab": False,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, indent=2) + "\n")
        if not winner:
            raise RuntimeError("Flagsmith result did not pass the rollout gate; see evidence")
        apply_winner(config, admin, path, winner, list(representatives.values()))
        evidence["rollout_verified_in_lab"] = True
        output.write_text(json.dumps(evidence, indent=2) + "\n")
        print(json.dumps({key: evidence[key] for key in ("cohorts", "winner", "rollout_verified_in_lab")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".local/flagsmith-experiment-results.json"))
    args = parser.parse_args()
    run_experiment(os.environ, args.output)
