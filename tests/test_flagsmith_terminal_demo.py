from scripts import flagsmith_terminal_demo as demo


def record(status, mode, attempts):
    return {"status": status, "policy": {"source": "flagsmith", "mode": mode},
            "attempts": [{"classification": "dependency"} for _ in range(attempts)]}


def test_two_real_runner_policies_must_show_an_isolated_enforcement_effect():
    assert demo.compare_outage(
        [record("blocked", "enforce", 2)] * 2 + [record("blocked", "enforce", 1)] * 2,
        [record("failed", "observe", 3)] * 4,
    ) == (6, 12)


def test_equal_attempts_cannot_count_as_verified():
    import pytest
    with pytest.raises(ValueError, match="fewer"):
        demo.compare_outage(
            [record("blocked", "enforce", 3)] * 4,
            [record("failed", "observe", 3)] * 4,
        )
