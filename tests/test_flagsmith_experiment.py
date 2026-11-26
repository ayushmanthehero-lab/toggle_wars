from copy import deepcopy

import pytest

from scripts.flagsmith_experiment import measure_batch, select_winner


def result(status, attempts, mode, condition):
    return {
        "status": status, "coordination_error": False, "policy": {"mode": mode, "source": "flagsmith"},
        "attempts": [{"classification": condition} for _ in range(attempts)],
    }


def test_batch_measures_actual_attempts_without_mislabelling_blocked():
    runs = [result("blocked", n, "enforce", "dependency") for n in (2, 2, 1, 1)]
    assert measure_batch(runs, mode="enforce", scenario="outage") == 6
    with pytest.raises(ValueError):
        measure_batch([*runs[:3], result("passed", 1, "enforce", "passed")], mode="enforce", scenario="outage")


def test_assignment_and_guardrails_are_checked():
    with pytest.raises(ValueError):
        measure_batch([result("passed", 1, "observe", "passed")], mode="enforce", scenario="healthy")
    with pytest.raises(ValueError):
        measure_batch([result("failed", 2, "observe", "assertion")], mode="observe", scenario="assertion")


def test_only_complete_when_counts_and_provider_result_agree():
    payload = {"srm_p_value": 0.9, "metrics": [{"metric_id": 5, "variants": {
        "control": {"n": 60, "sum": 720.0}, "enforce": {"n": 60, "sum": 360.0}
    }, "inference": {"enforce": {"chance_to_win": 0.999, "lift": -0.5}}}]}
    assert select_winner(payload, 5, {"control": (60, 720), "enforce": (60, 360)}) == "enforce"
    assert select_winner(payload, 5, {"control": (60, 719), "enforce": (60, 360)}) is None
    bad = deepcopy(payload)
    bad["metrics"][0]["inference"]["enforce"]["chance_to_win"] = 0.8
    assert select_winner(bad, 5, {"control": (60, 720), "enforce": (60, 360)}) is None
    bad = deepcopy(payload)
    bad["srm_p_value"] = 0.0001
    assert select_winner(bad, 5, {"control": (60, 720), "enforce": (60, 360)}) is None


def test_control_can_win_if_enforce_increases_executions():
    payload = {"srm_p_value": 0.9, "metrics": [{"metric_id": 5, "variants": {
        "control": {"n": 60, "sum": 360.0}, "enforce": {"n": 60, "sum": 720.0}
    }, "inference": {"enforce": {"chance_to_win": 0.01, "lift": 1.0}}}]}
    assert select_winner(payload, 5, {"control": (60, 360), "enforce": (60, 720)}) == "control"


@pytest.mark.parametrize("url", [
    "https://127.0.0.1:8000/api/v1/", "http://example.com:8000/api/v1/",
    "http://user:password@127.0.0.1:8000/api/v1/", "http://127.0.0.1/api/v1/",
    "http://127.0.0.1:8000/api/v1/?private=1",
])
def test_reject_non_lab_endpoints(url):
    from scripts.flagsmith_experiment import loopback

    with pytest.raises(ValueError):
        loopback(url, path="/api/v1/")


def test_uses_observed_50_50_counts_if_flagsmith_does_not_report_srm():
    from scripts.flagsmith_experiment import split_check

    assert split_check(None, {"control": (74, 888), "enforce": (70, 419)}) > 0.7
    assert split_check(None, {"control": (94, 1128), "enforce": (50, 300)}) < 0.001
    assert split_check(0.0001, {"control": (72, 864), "enforce": (72, 432)}) == 0.0001
