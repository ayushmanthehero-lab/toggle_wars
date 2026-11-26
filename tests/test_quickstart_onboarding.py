import importlib
from pathlib import Path

import httpx
import pytest


def mod():
    return importlib.import_module("scripts.ci_guard_onboard")


def test_doctor_checks_only_get_and_refuses_different_policy():
    m = mod()
    requests = []
    selected = {"mode": "enforce", "max_job_retries": 2, "shared_dependency_retry_budget": 2,
                "dependency_cooldown_seconds": 30, "source": "flagsmith", "error": None}
    def handle(req):
        requests.append((req.method, req.url.path))
        assert req.headers["Authorization"] == "Bearer cg_proj_example"
        assert req.url.path == "/api/setup"
        assert req.url.params["repository"] == "team/consumer"
        return (httpx.Response(200, json={"dependency_visible": True, "policy": selected}) if req.url.params["dependency"] == "inventory"
                else httpx.Response(200, json={"dependency_visible": False, "policy": None}))
    with httpx.Client(base_url="https://coord.example", transport=httpx.MockTransport(handle),
                      headers={"Authorization": "Bearer cg_proj_example"}) as client:
        assert m.check_coordinator(client, "team/consumer", "inventory", selected) == "enforce"
        with pytest.raises(m.SetupError, match="does not match"):
            m.check_coordinator(client, "team/consumer", "inventory", dict(selected, mode="observe"))
        with pytest.raises(m.SetupError, match="not visible"):
            m.check_coordinator(client, "team/consumer", "billing", selected)
    assert all(method == "GET" for method, _ in requests)


@pytest.mark.parametrize("value", ["http://localhost:9000", "https://name:secret@host", "https://host/?",
                                   "https://127.0.0.1", "https://host/#secret", "https://host\\wrong"])
def test_endpoint_rejects_credentials_and_unreachable_forms(value):
    with pytest.raises(mod().SetupError, match="HTTPS"):
        mod().public_https(value)


def test_starter_has_no_live_pr_path_and_pins_ci_guard():
    raw = mod().starter()
    pr, staging = raw.split("  guarded-staging:", 1)
    assert "permissions:\n  contents: read" in raw
    assert "pull_request" in pr and "secrets." not in pr
    assert "workflow_dispatch" in staging and "ref_protected" in staging
    assert "environment: ci-guard-staging" in staging
    assert "toggle_wars@76bc04e591062012104a30161d2b5a92b0b6699b" in staging
    assert "pull_request_target" not in raw and "secrets: inherit" not in raw


def test_compose_has_no_published_ports_and_onboarding_no_lab_dependency():
    raw = Path("compose.quickstart.yaml").read_text()
    assert "    internal: true" in raw and "    ports:" not in raw
    terminal, onboard = raw.split("  onboard:", 1)
    assert "network_mode: service:flagsmith" in terminal
    assert "seed: {condition: service_completed_successfully}" in terminal
    assert "depends_on" not in onboard


def test_packaged_lab_only_accepts_loopback_fixture(tmp_path):
    quick = importlib.import_module("scripts.ci_guard_quickstart")
    cfg = tmp_path / "terminal.env"
    cfg.write_text("FLAGSMITH_API_URL=https://outside.example/api/v1/\n")
    with pytest.raises(quick.LabError, match="disposable"):
        quick.lab_config(cfg)


def test_outage_receipt_never_calls_failed_jobs_passed():
    quick = importlib.import_module("scripts.ci_guard_quickstart")
    reports = [{"attempts": [{}, {}], "status": "failed"}, {"attempts": [], "status": "blocked"}]
    assert quick.outage_summary(reports) == (2, 0)
    with pytest.raises(quick.LabError, match="incomplete"):
        quick.outage_summary([{"status": "error"}])
