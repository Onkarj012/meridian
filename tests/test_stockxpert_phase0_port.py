import math

import numpy as np
import pytest

from evidence.backtest import run_backtest
from evidence.costs import IndianEquityCostModel, calculate_round_trip_cost
from evidence.walkforward import run_walk_forward
from models.calibration import fit_isotonic_calibrator
from models.splits import CrossSectionalSplits, SplitAssignment, assert_no_leakage


def test_leakage_assertion_catches_planted_symbol_leak() -> None:
    splits = CrossSectionalSplits(
        train=SplitAssignment("train", ("AAA",), "2020-01-01", "2020-12-31"),
        val=SplitAssignment("val", ("AAA",), "2021-01-11", "2021-12-31"),
        test=SplitAssignment("test", ("BBB",), "2022-01-11", "2022-12-31"),
        blind=SplitAssignment("blind", ("CCC",), "2023-01-11", "2023-12-31"),
    )

    with pytest.raises(AssertionError, match="multiple splits"):
        assert_no_leakage([], splits)


def test_embargo_is_respected_between_split_windows() -> None:
    violating = CrossSectionalSplits(
        train=SplitAssignment("train", ("AAA",), "2020-01-01", "2020-12-31"),
        val=SplitAssignment("val", ("BBB",), "2021-01-05", "2021-12-31"),
        test=SplitAssignment("test", ("CCC",), "2022-01-11", "2022-12-31"),
        blind=SplitAssignment("blind", ("DDD",), "2023-01-11", "2023-12-31"),
        embargo_days=10,
    )

    with pytest.raises(AssertionError, match="embargo"):
        assert_no_leakage([], violating)

    valid = CrossSectionalSplits(
        train=SplitAssignment("train", ("AAA",), "2020-01-01", "2020-12-31"),
        val=SplitAssignment("val", ("BBB",), "2021-01-11", "2021-12-31"),
        test=SplitAssignment("test", ("CCC",), "2022-01-11", "2022-12-31"),
        blind=SplitAssignment("blind", ("DDD",), "2023-01-11", "2023-12-31"),
        embargo_days=10,
    )
    assert_no_leakage([{"symbol": "BBB", "date": "2021-02-01", "split": "val"}], valid)


def test_isotonic_calibration_is_monotone() -> None:
    calibrator = fit_isotonic_calibrator(
        [0.1, 0.2, 0.4, 0.6, 0.8, 0.9],
        [0, 0, 0, 1, 1, 1],
    )
    predicted = calibrator.predict([0.15, 0.35, 0.55, 0.75])

    assert list(predicted) == sorted(predicted)
    assert all(0.0 <= item <= 1.0 for item in predicted)


def test_cost_model_matches_hand_computed_delivery_and_intraday_round_trips() -> None:
    model = IndianEquityCostModel(brokerage_cap_per_order=None, slippage_bps_by_bucket={"liquid": 5.0, "unknown": 5.0})

    # Delivery on Rs 10,000 each side:
    # buy = brokerage 5 + stamp 1.5 + exchange 0.322 + SEBI 0.01 + GST((5 + 0.322)*18%) 0.958 + slip 5
    # sell = brokerage 5 + STT 10 + exchange 0.322 + SEBI 0.01 + GST((5 + 0.322)*18%) 0.958 + slip 5
    # total = 34.08 rupees = 34.08 bps on Rs 10,000.
    delivery = model.round_trip(10_000, product="delivery", liquidity_bucket="liquid")
    assert delivery["total"] == pytest.approx(34.08, abs=0.001)
    assert delivery["total_bps"] == pytest.approx(34.08, abs=0.001)

    # Intraday changes STT to 0.025% on sell and stamp to 0.003% on buy:
    # buy = 5 + 0.3 + 0.322 + 0.01 + 0.958 + 5 = 11.59
    # sell = 5 + 2.5 + 0.322 + 0.01 + 0.958 + 5 = 13.79
    # total = 25.38 rupees = 25.38 bps.
    intraday = model.round_trip(10_000, product="intraday", liquidity_bucket="liquid")
    assert intraday["total"] == pytest.approx(25.38, abs=0.001)
    assert intraday["total_bps"] == pytest.approx(25.38, abs=0.001)


def test_backtest_uses_full_cost_model_ignoring_flat_cost_config(tmp_path) -> None:
    data = tmp_path / "gold.csv"
    data.write_text(
        "timestamp,symbol,confidence,long_label,label_excluded,notional,product,liquidity_bucket\n"
        "2025-01-01T09:15:00+05:30,ABC,0.9,LONG_SUCCESS,false,10000,delivery,liquid\n",
        encoding="utf-8",
    )
    config = tmp_path / "config.yml"
    config.write_text("top_k: 1\nmin_confidence: 0.5\nfull_cost_bps: 0\ncost_bps: 0\n", encoding="utf-8")

    report = run_backtest(data, tmp_path / "report.md", config)
    cost_bps = calculate_round_trip_cost(10_000, product="delivery", liquidity_bucket="liquid")["total_bps"]
    assert report["recommendations"][0]["cost_bps"] == pytest.approx(cost_bps)
    assert report["net_score_after_costs"] == pytest.approx(1.0 - cost_bps / 10000)


def test_walk_forward_selects_by_worst_fold_not_mean() -> None:
    folds = [{"name": "f1"}, {"name": "f2"}, {"name": "f3"}]
    grid = [{"id": "mean_best"}, {"id": "worst_best"}]
    returns = {
        "mean_best": [100.0, 100.0, -50.0],
        "worst_best": [20.0, 20.0, 20.0],
    }

    def evaluator(payload):
        config_id = payload["config"]["id"]
        fold_index = int(payload["fold"]["name"][1:]) - 1
        return {"net_return": returns[config_id][fold_index], "trades": 10}

    result = run_walk_forward(folds, evaluator, grid=grid, grid_committed=True, min_trades=30)

    assert result["selected_config_id"] == "worst_best"
    assert result["selected"]["worst_fold"] == 20.0
    assert np.mean(returns["mean_best"]) > np.mean(returns["worst_best"])
