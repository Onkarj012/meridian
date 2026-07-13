"""Synthetic gate fixtures for registered C1 verdicts."""
from __future__ import annotations

from copy import deepcopy

from evidence.c1_gates import classify_6e, evaluate_6a_holdout, evaluate_6a_wf, evaluate_6b


def _wf_fixture() -> dict:
    return {
        "wf": {
            "mean_daily_pnl": 1.0,
            "sharpe": 0.80,
            "trades": 300,
            "stress_5_bps": {"mean": 0.2, "sharpe": 0.1},
            "beats_matched_baselines": True,
            "operationally_executable": True,
        }
    }


def test_6a_passes_and_each_enforced_leg_can_be_toggled():
    artifact = _wf_fixture()
    assert evaluate_6a_wf(artifact)["passed"] is True
    toggles = [
        ("mean_daily_pnl", -1.0),
        ("sharpe", 0.2),
        ("trades", 299),
        ("beats_matched_baselines", False),
        ("operationally_executable", False),
    ]
    for key, value in toggles:
        changed = deepcopy(artifact)
        changed["wf"][key] = value
        assert evaluate_6a_wf(changed)["passed"] is False, key
    assert evaluate_6a_wf(artifact)["legs"]["quarter_era_bucket_domination"]["status"] == "REPORT-ONLY"


def test_6a_trade_minimum_is_a_hard_fail():
    artifact = _wf_fixture()
    artifact["wf"]["trades"] = 299
    verdict = evaluate_6a_wf(artifact)
    assert verdict["passed"] is False
    assert verdict["legs"]["wf_trade_minimum"]["hard_fail"] is True


def test_6b_requires_both_delta_sharpe_and_paired_ci_low():
    artifact = {
        "wf": {"delta_sharpe": 0.30, "paired_mbb_ci_low": 0.01},
        "holdout": {"delta_sharpe": 0.30, "paired_mbb_ci_low": 0.01},
    }
    assert evaluate_6b(artifact)["passed"] is True
    changed = deepcopy(artifact)
    changed["holdout"]["delta_sharpe"] = 0.24
    assert evaluate_6b(changed)["passed"] is False
    changed = deepcopy(artifact)
    changed["wf"]["paired_mbb_ci_low"] = 0.0
    assert evaluate_6b(changed)["passed"] is False


def test_6e_classification_table():
    assert classify_6e(True, True)["classification"] == "winner"
    assert classify_6e(True, False)["classification"] == "thin-positive"
    assert classify_6e(False, True)["classification"] == "dead"


def test_holdout_6a_legs_are_separate_from_wf_evaluation():
    artifact = {
        "holdout": {
            "sharpe": 0.50,
            "trades": 150,
            "trades_per_holdout_half": [40, 40],
            "post_2026_04_trades": 30,
        }
    }
    assert evaluate_6a_holdout(artifact)["passed"] is True
    assert "holdout_sharpe" not in evaluate_6a_wf(_wf_fixture())["legs"]
