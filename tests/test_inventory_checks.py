"""Required inventory checks verify real test execution, not service availability alone."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from threading import Thread

import pytest

from examples.inventory_service import Handler, InventoryServer


ROOT = Path(__file__).resolve().parents[1]


def run_check(name, report, *, env=None, args=()):
    child_env = {key: value for key, value in os.environ.items()
                 if not key.startswith(("CI_GUARD_", "FLAGSMITH_", "PYTEST_"))}
    child_env.pop("GITHUB_REPOSITORY", None)
    child_env.update(env or {})
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / f"check_{name}_inventory.py"),
         "--report", str(report), *args],
        cwd=ROOT, env=child_env, capture_output=True, text=True, timeout=25,
    )
    assert report.is_file(), (result.returncode, result.stderr)
    return result, json.loads(report.read_text())


@contextmanager
def broken_shared_inventory():
    hits = []

    class CountingHandler(Handler):
        def do_GET(self):
            hits.append(self.path)
            super().do_GET()

    with InventoryServer(("127.0.0.1", 0), CountingHandler) as server:
        server.healthy = False
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", hits
        finally:
            server.shutdown()
            thread.join(timeout=2)


def assert_fixture_stopped(report):
    with socket.socket() as client:
        client.settimeout(0.3)
        assert client.connect_ex(("127.0.0.1", report["fixture_port"])) != 0


def test_isolated_inventory_passes_actual_pytest_without_touching_broken_shared_service(tmp_path):
    with broken_shared_inventory() as (shared, hits):
        result, report = run_check("isolated", tmp_path / "isolated.json", env={
            "INVENTORY_URL": shared, "CI_GUARD_LIVE_INVENTORY_URL": shared,
            "CI_GUARD_URL": shared, "FLAGSMITH_API_URL": shared,
            "CI_GUARD_PROJECT_TOKEN": "cg_proj_fixture_should_not_escape",
            "DEMO_ASSERTION_FAILURE": "true", "PYTEST_ADDOPTS": "--collect-only",
        })
    assert hits == []
    assert (result.returncode, report["status"], report["scope"]) == (0, "passed", "isolated_pr")
    assert report["attempts"] == 1 and report["pytest_outcomes"]["passed"] == 1
    assert "cg_proj_fixture_should_not_escape" not in json.dumps(report) + result.stdout + result.stderr
    assert_fixture_stopped(report)


def test_isolated_inventory_assertion_fails_even_when_fixture_is_healthy(tmp_path):
    result, report = run_check("isolated", tmp_path / "isolated.json",
                               args=("--simulate-assertion-failure",))
    assert (result.returncode, report["status"]) == (1, "failed")
    assert report["attempts"] == 1 and report["pytest_outcomes"]["failed"] == 1
    assert_fixture_stopped(report)


def test_concurrent_fixture_runs_do_not_share_assertion_settings_or_ports(tmp_path):
    with ThreadPoolExecutor(max_workers=2) as pool:
        broken = pool.submit(run_check, "isolated", tmp_path / "failed.json",
                             args=("--simulate-assertion-failure",))
        healthy = pool.submit(run_check, "isolated", tmp_path / "passed.json")
        failed_result, failed_report = broken.result()
        passed_result, passed_report = healthy.result()
    assert (failed_result.returncode, passed_result.returncode) == (1, 0)
    assert failed_report["fixture_port"] != passed_report["fixture_port"]


def test_live_missing_configuration_is_error_with_zero_executions(tmp_path):
    result, report = run_check("live", tmp_path / "live.json")
    assert (result.returncode, report["status"], report["scope"]) == (3, "error", "live_release")
    assert report["attempts"] == 0
    assert "missing" in report["reason"].lower()


def test_live_unverified_flagsmith_never_passes_on_zero_exit():
    from scripts.check_live_inventory import verify_guard_success

    policy = {"source": "flagsmith", "mode": "enforce", "error": None, "fingerprint": "a" * 64}
    result = {
        "status": "passed", "exit_code": 0, "policy": policy,
        "coordination_error": None, "decisions": [{"action": "run", "policy": policy},
                                                   {"action": "stop", "policy": policy}],
        "attempts": [{"verification": "pytest", "classification": "passed", "exit_code": 0,
                      "pytest_outcomes": {"passed": 1, "collected": 1, "failed": 0, "errors": 0,
                                          "skipped": 0, "xfailed": 0, "xpassed": 0, "deselected": 0}}],
    }
    assert verify_guard_success(result, "enforce")
    assert not verify_guard_success({**result, "decisions": [
        {"action": "run", "policy": {**policy, "mode": "off", "error": "Invalid policy"}},
        {"action": "stop", "policy": policy},
    ]}, "enforce")
    assert not verify_guard_success({**result, "attempts": []}, "enforce")
    assert not verify_guard_success(result, "observe")


@pytest.mark.parametrize("key,value", [
    ("CI_GUARD_URL", "http://127.0.0.1:1"),
    ("CI_GUARD_LIVE_INVENTORY_URL", "https://secret:password@inventory.example.test"),
    ("CI_GUARD_EXPECTED_MODE", "off"),
    ("CI_GUARD_PROJECT_TOKEN", "fixture_not_project_token"),
])
def test_live_invalid_configuration_is_rejected_without_echoing_values(tmp_path, key, value):
    env = {
        "CI_GUARD_URL": "https://guard.example.test",
        "CI_GUARD_LIVE_INVENTORY_URL": "https://inventory.example.test",
        "CI_GUARD_PROJECT_TOKEN": "cg_proj_dummy_for_validation",
        "CI_GUARD_DEPENDENCY": "inventory-live",
        "GITHUB_REPOSITORY": "fixture/repo",
        key: value,
    }
    result, report = run_check("live", tmp_path / "live.json", env=env)
    assert (result.returncode, report["status"], report["attempts"]) == (3, "error", 0)
    combined = json.dumps(report) + result.stdout + result.stderr
    assert "secret:password" not in combined and "dummy_for_validation" not in combined


@pytest.fixture
def live_loopback(tmp_path, monkeypatch):
    """Real HTTPS, SDK evaluation, scoped token, API, SQLite and subprocess."""
    import importlib
    import ipaddress
    import ssl
    import time
    from datetime import datetime, timedelta, timezone
    from urllib.error import HTTPError
    from urllib.request import HTTPSHandler, ProxyHandler, build_opener

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    from fastapi.testclient import TestClient
    import uvicorn

    from ci_guard.db import dump

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "CI Guard loopback test")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().issuer_name(name).subject_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    key_path, cert_path = tmp_path / "key.pem", tmp_path / "cert.pem"
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    state = {"healthy": True, "mode": "enforce", "restore_policy_on_execution": False,
             "shared_budget": 0, "executions": 0, "health_checks": 0,
             "redirect_target": None, "redirect_hits": 0}

    class InventoryAndFlagsmith(Handler):
        def flag_states(self):
            values = {"mode": state["mode"], "max_job_retries": 2,
                      "shared_dependency_retry_budget": state["shared_budget"],
                      "dependency_cooldown_seconds": 60}
            return [{"enabled": True, "feature_state_value": value,
                     "feature": {"name": "ci_guard_" + name, "id": i}}
                    for i, (name, value) in enumerate(values.items(), 1)]

        def do_GET(self):
            if self.path == "/flagsmith/flags/":
                return self.respond(200, self.flag_states())
            if self.path == "/incorrect-endpoint":
                state["redirect_hits"] += 1
                return self.respond(200, {"stock": 42})
            if self.path == "/inventory":
                state["executions"] += 1
                if state["restore_policy_on_execution"]:
                    state["mode"] = "enforce"
                if state["redirect_target"]:
                    self.send_response(302)
                    self.send_header("Location", state["redirect_target"])
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
            if self.path == "/health":
                state["health_checks"] += 1
            self.server.healthy = state["healthy"]
            return super().do_GET()

        def do_POST(self):
            if self.path == "/flagsmith/identities/":
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if data.get("identifier") != "repo:fixtures/repo":
                    return self.respond(400, {})
                return self.respond(200, {"flags": self.flag_states()})
            return self.respond(404, {})

    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert_path, key_path)
    with InventoryServer(("127.0.0.1", 0), InventoryAndFlagsmith) as inventory:
        inventory.socket = tls.wrap_socket(inventory.socket, server_side=True)
        url = f"https://127.0.0.1:{inventory.server_port}"
        thread = Thread(target=inventory.serve_forever, daemon=True)
        thread.start()
        try:
            monkeypatch.setenv("FLAGSMITH_ENVIRONMENT_KEY", "local-protocol-fixture")
            monkeypatch.setenv("FLAGSMITH_API_URL", url + "/flagsmith/")
            monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(cert_path))
            monkeypatch.setenv("NO_PROXY", "127.0.0.1")
            monkeypatch.setenv("no_proxy", "127.0.0.1")
            monkeypatch.setenv("CI_GUARD_ADMIN_TOKEN", "fixture-only-admin-token")
            monkeypatch.delenv("CI_GUARD_DEMO_MODE", raising=False)
            app = importlib.import_module("ci_guard.app").create_app(tmp_path / "live.sqlite")
            request = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context(cafile=cert_path)))

            def check_health(dependency):
                # Only the TLS trust boundary is substituted: actual HTTPS status
                # still drives the real coordinator's outage decision.
                try:
                    with request.open(dependency["health_url"], timeout=2) as response:
                        return "healthy", f"HTTP {response.status}"
                except HTTPError as error:
                    return "unhealthy", f"HTTP {error.code}"

            app.state.health_probe = check_health
            admin = {"Authorization": "Bearer fixture-only-admin-token"}
            api = TestClient(app)
            assert api.post("/api/dependencies", headers=admin, json={
                "id": "inventory-live", "name": "Inventory", "health_url": url + "/health",
                "error_patterns": ["INVENTORY_DEPENDENCY_UNAVAILABLE"],
            }).status_code == 201
            with app.state.db.transaction() as db:
                db.execute("INSERT INTO github_connections VALUES (?,?,?,?,?)",
                           ("fixture", dump({"id": "fixture"}), None, "environment", None))
                db.execute("INSERT INTO github_projects VALUES (?,?,?,?)",
                           ("project", "fixture", 1, dump({"id": "project", "full_name": "fixtures/repo"})))
            token = app.state.github_store.rotate_runner_token("project", ["inventory-live"])["token"]
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                config = uvicorn.Config(app, ssl_keyfile=key_path, ssl_certfile=cert_path,
                                        log_level="critical", access_log=False)
                server = uvicorn.Server(config)
                api_thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
                api_thread.start()
                try:
                    deadline = time.monotonic() + 5
                    while not server.started:
                        assert api_thread.is_alive() and time.monotonic() < deadline
                        time.sleep(0.02)
                    yield {
                        "CI_GUARD_URL": f"https://127.0.0.1:{listener.getsockname()[1]}",
                        "CI_GUARD_LIVE_INVENTORY_URL": url,
                        "CI_GUARD_PROJECT_TOKEN": token, "CI_GUARD_DEPENDENCY": "inventory-live",
                        "GITHUB_REPOSITORY": "fixtures/repo", "SSL_CERT_FILE": str(cert_path),
                    }, state, lambda: api.get("/api/runs", headers=admin).raise_for_status().json()
                finally:
                    server.should_exit = True
                    api_thread.join(timeout=5)
        finally:
            inventory.shutdown()
            thread.join(timeout=2)


def test_live_outage_is_blocked_and_underlying_tests_are_not_marked_passed(tmp_path, live_loopback):
    env, state, stored_runs = live_loopback
    state["healthy"] = False
    run, report = run_check("live", tmp_path / "live.json", env=env)
    assert (run.returncode, report["status"], report["attempts"]) == (2, "blocked", 1)
    assert state["executions"] == 1 and state["health_checks"] == 1
    assert stored_runs()[0]["status"] == "blocked"
    published = json.dumps(report) + run.stdout + run.stderr
    assert env["CI_GUARD_PROJECT_TOKEN"] not in published
    assert env["CI_GUARD_LIVE_INVENTORY_URL"] not in published


def test_live_rejects_invalid_applied_policy_even_if_command_and_terminal_policy_pass(tmp_path, live_loopback):
    env, state, stored_runs = live_loopback
    state.update(mode="bad-fixture-mode", restore_policy_on_execution=True)
    run, report = run_check("live", tmp_path / "live.json", env=env)
    assert (run.returncode, report["status"], report["attempts"]) == (3, "error", 1)
    assert report["pytest_outcomes"]["passed"] == 1 and state["mode"] == "enforce"
    assert stored_runs()[0]["policy"]["error"]
    assert stored_runs()[0]["status"] == "passed"


def test_live_requires_actual_success_with_expected_flagsmith_policy(tmp_path, live_loopback):
    env, state, stored_runs = live_loopback
    run, report = run_check("live", tmp_path / "live.json", env=env)
    assert (run.returncode, report["status"], report["attempts"]) == (0, "passed", 1)
    assert report["pytest_outcomes"]["passed"] == 1 and report["policy_source"] == "flagsmith"
    assert state["executions"] == 1 and stored_runs()[0]["status"] == "passed"


def test_live_rejects_mismatched_registered_health_target_before_execution(tmp_path, live_loopback):
    env, state, stored_runs = live_loopback
    env["CI_GUARD_LIVE_INVENTORY_URL"] += "/wrong-inventory"
    run, report = run_check("live", tmp_path / "live.json", env=env)
    assert (run.returncode, report["status"], report["attempts"]) == (3, "error", 0)
    assert "health URL must match" in report["reason"]
    assert state["executions"] == 0 and stored_runs() == []


def test_live_does_not_follow_inventory_redirect_to_another_endpoint(tmp_path, live_loopback):
    env, state, stored_runs = live_loopback
    state["redirect_target"] = env["CI_GUARD_LIVE_INVENTORY_URL"] + "/incorrect-endpoint"
    result, report = run_check("live", tmp_path / "live.json", env=env)
    assert (result.returncode, report["status"], report["attempts"]) == (1, "failed", 1)
    assert state["redirect_hits"] == 0 and state["executions"] == 1
    assert stored_runs()[0]["status"] == "failed"
