import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest


@pytest.fixture
def terminal():
    from scripts.flagsmith_terminal import FlagsmithTerminal, TerminalError

    envs = [
        {"id": i, "name": name, "api_key": f"credential-{i}", "project": 7,
         "minimum_change_request_approvals": None, "use_v2_feature_versioning": False}
        for i, name in [(10, "Canary QA"), (11, "Development"), (12, "Production")]
    ]
    features = [
        {"id": i, "name": name, "type": kind, "tags": tags, "is_archived": False,
         "multivariate_options": []}
        for i, name, kind, tags in [
            (1, "queue_panel", "STANDARD", []), (2, "ci_guard_mode", "STANDARD", []),
            (3, "experiment", "MULTIVARIATE", []), (4, "locked", "STANDARD", [5]),
        ]
    ]
    states = {env["id"]: {
        f["id"]: {"id": env["id"] * 10 + f["id"], "feature": f["id"],
                  "environment": env["id"], "enabled": f["id"] != 1,
                  "feature_state_value": "observe" if f["id"] == 2 else None,
                  "identity": None, "feature_segment": None}
        for f in features
    } for env in envs}
    writes = []

    def handle(request):
        assert request.headers["Authorization"] == "Api-Key secret"
        assert request.url.host == "localhost"
        path = request.url.path.removeprefix("/api/v1/")
        if path == "environments/":
            assert request.url.params["project"] == "7"
            page = request.url.params["page"]
            # Never follow this absolute pagination URL; use the trusted endpoint.
            return httpx.Response(200, json={"results": envs[:1] if page == "1" else envs[1:],
                                             "next": "https://no.example/elsewhere" if page == "1" else None})
        if path == "projects/7/features/":
            return httpx.Response(200, json=features)
        if path == "projects/7/tags/":
            return httpx.Response(200, json=[{"id": 5, "label": "protected"}])
        eid = int(path.split("/")[1].split("-")[-1])
        if request.method == "PATCH":
            state_id = int(path.split("/")[-2])
            write = json.loads(request.content)
            writes.append((eid, state_id, write))
            states[eid][state_id % 10].update(write)
            return httpx.Response(200, json=states[eid][state_id % 10])
        if path.split("/")[-2].isdigit():
            return httpx.Response(200, json=states[eid][int(path.split("/")[-2]) % 10])
        return httpx.Response(200, json=list(states[eid].values()))

    client = FlagsmithTerminal("http://localhost:8000/api/v1/", "secret", 7, {10, 12},
                               transport=httpx.MockTransport(handle))
    yield client, states, writes, TerminalError, envs
    client.close()


def test_entry_point_has_terminal_help():
    cli = Path(__file__).parents[1] / "scripts/flagsmith_terminal.py"
    result = subprocess.run([sys.executable, str(cli), "--help"], capture_output=True, text=True)
    assert result.returncode == 0 and "Flagsmith" in result.stdout


def test_discover_paginated_environment_names_and_compare_base_states(terminal):
    client, _, _, _, _ = terminal
    environments, matrix = client.snapshot()
    assert [e.name for e in environments] == ["Canary QA", "Development", "Production"]
    assert [matrix[e.id]["queue_panel"].enabled for e in environments] == [False, False, False]
    assert "credential-" not in repr(environments)


def test_ordinary_toggle_and_ci_policy_only_patch_the_selected_environment(terminal):
    client, states, writes, _, _ = terminal
    assert client.apply(client.preview(10, "queue_panel", "on")).enabled
    assert client.apply(client.preview(10, "ci_guard_mode", "enforce")).value == "enforce"
    assert writes == [(10, 101, {"enabled": True}), (10, 102, {"feature_state_value": "enforce"})]
    assert states[11][1]["enabled"] is False
    assert states[11][2]["feature_state_value"] == "observe"


@pytest.mark.parametrize("env,flag,value", [
    (11, "queue_panel", "on"), (12, "queue_panel", "on"), (99, "queue_panel", "on"),
    (10, "experiment", "off"), (10, "locked", "off"), (10, "ci_guard_mode", "oops"),
])
def test_unapproved_targets_never_write(terminal, env, flag, value):
    client, _, writes, error, _ = terminal
    with pytest.raises(error):
        client.preview(env, flag, value)
    assert not writes


def test_changed_preview_is_not_replayed(terminal):
    client, states, writes, error, _ = terminal
    preview = client.preview(10, "queue_panel", "on")
    states[10][1]["enabled"] = True
    with pytest.raises(error, match="changed"):
        client.apply(preview)
    assert not writes


def test_environment_approval_or_versioning_is_read_only(terminal):
    client, _, writes, error, envs = terminal
    envs[0]["use_v2_feature_versioning"] = True
    with pytest.raises(error, match="read-only"):
        client.preview(10, "queue_panel", "on")
    assert not writes


def test_unavailable_api_does_not_echo_sensitive_error_text():
    from scripts.flagsmith_terminal import FlagsmithTerminal, TerminalError
    def down(request):
        return httpx.Response(503, text="secret diagnostics")
    client = FlagsmithTerminal("http://localhost:8000/api/v1/", "secret", 7, set(),
                               transport=httpx.MockTransport(down))
    with pytest.raises(TerminalError, match="503") as failure:
        client.snapshot()
    assert "secret" not in str(failure.value)


def test_typed_confirmation_can_be_cancelled(terminal):
    from scripts.flagsmith_terminal import confirm_and_apply
    client, _, writes, _, _ = terminal
    change = client.preview(10, "queue_panel", "on")
    assert confirm_and_apply(client, change, confirm=lambda _: "no") is None
    assert not writes
