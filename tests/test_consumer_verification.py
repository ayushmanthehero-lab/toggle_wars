"""Consumer evidence validation uses real unittest output, including its zero-test exit."""

import subprocess
import sys

import pytest


def unittest_result(tmp_path, source):
    (tmp_path / "test_consumer_case.py").write_text(source)
    return subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(tmp_path), "-v"],
                          capture_output=True, text=True, timeout=10)


def test_successful_unittest_count_is_taken_from_actual_output(tmp_path):
    from scripts.verify_consumer import parse_unittest_result
    result = unittest_result(tmp_path, "import unittest\nclass Cases(unittest.TestCase):\n def test_one(self): self.assertEqual(1, 1)\n def test_two(self): self.assertTrue(True)\n")
    counts = parse_unittest_result(result.stderr, expected=2)
    assert result.returncode == 0
    assert counts["tests_run"] == counts["passed"] == 2
    assert counts["skipped"] == 0


@pytest.mark.parametrize("source, expected", [
    ("# An empty discovery exits zero.\n", 0),
    ("import unittest\nclass Cases(unittest.TestCase):\n @unittest.skip('fixture')\n def test_one(self): pass\n", 1),
    ("import unittest\nclass Cases(unittest.TestCase):\n def test_one(self): self.fail('real failure')\n", 1),
    ("import unittest\nclass Cases(unittest.TestCase):\n def test_one(self): pass\n", 34),
])
def test_unexecuted_failed_or_unexpected_suite_cannot_verify_success(tmp_path, source, expected):
    from scripts.verify_consumer import VerificationError, parse_unittest_result
    result = unittest_result(tmp_path, source)
    with pytest.raises(VerificationError):
        parse_unittest_result(result.stderr, expected=expected)


def test_controlled_counts_are_recomputed_from_actual_jobs():
    from scripts.verify_consumer import controlled_comparison
    evidence = {"runs": [
        {"scenario": "outage", "mode": "observe", "attempts": 12,
         "jobs": [{"status": "failed", "attempts": 3}] * 4},
        {"scenario": "outage", "mode": "enforce", "attempts": 6,
         "jobs": [{"status": "blocked", "attempts": 3}, {"status": "blocked", "attempts": 1},
                  {"status": "blocked", "attempts": 1}, {"status": "blocked", "attempts": 1}]},
    ]}
    comparison = controlled_comparison(evidence)
    assert comparison == {"concurrent_jobs": 4, "observe_attempts": 12, "enforce_attempts": 6,
                          "shared_retry_budget": 2, "scope": "controlled_inventory_fixture"}


def test_controlled_comparison_rejects_invented_aggregate_counts():
    from scripts.verify_consumer import VerificationError, controlled_comparison
    evidence = {"runs": [
        {"scenario": "outage", "mode": "observe", "attempts": 12,
         "jobs": [{"status": "failed", "attempts": 1}] * 4},
        {"scenario": "outage", "mode": "enforce", "attempts": 1,
         "jobs": [{"status": "blocked", "attempts": 0}] * 4},
    ]}
    with pytest.raises(VerificationError):
        controlled_comparison(evidence)
