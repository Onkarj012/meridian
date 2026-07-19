"""Exactly twelve focused tests for the V0 intraday prediction path."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from contracts.intraday_targets import make_targets
from evidence.intraday_folds import assert_cutoff_safe, fold_table, intraday_folds, purge_rows
from evidence.intraday_metrics import confidence_metrics, session_block_bootstrap
from features.intraday_prediction import FORBIDDEN_COLUMNS, build_prediction_features
from models.intraday_confidence import calibrate_confidence, fit_correctness_calibrator
from scripts.run_intraday_prediction import run_experiment


def _bars(sessions: int = 4, bars: int = 75) -> pd.DataFrame:
    rows = []
    for day_index in range(sessions):
        day = pd.Timestamp("2025-06-01") + pd.Timedelta(days=day_index)
        times = pd.date_range(day + pd.Timedelta(hours=9, minutes=15), periods=bars, freq="min")
        close = 100 + day_index * 0.4 + np.sin(np.arange(bars) / 5) + np.arange(bars) * 0.01
        rows.append(pd.DataFrame({
            "datetime": times, "trade_date": day, "f_open": close - 0.02, "f_high": close + 0.08,
            "f_low": close - 0.08, "f_close": close, "realized_vol_30m": 0.0,
        }))
    return pd.concat(rows, ignore_index=True)


def test_targets_use_exact_future_close_formula() -> None:
    frame = _bars(sessions=1, bars=20)
    result = make_targets(frame, [15])
    row = result.iloc[0]
    expected = 10_000 * np.log(frame.iloc[15].f_close / frame.iloc[0].f_close)
    assert row["target_h15_mag_bps"] == pytest.approx(expected)


def test_flat_band_uses_floor_and_volatility_scaling() -> None:
    frame = _bars(sessions=1, bars=20)
    frame.loc[0, "realized_vol_30m"] = 0.0
    frame.loc[1, "realized_vol_30m"] = 1.0
    result = make_targets(frame, [15])
    assert result.iloc[0]["target_h15_band"] == pytest.approx(0.0002)
    expected = 0.25 * np.sqrt(15) / np.sqrt(252 * 375)
    assert result.iloc[1]["target_h15_band"] == pytest.approx(expected)


def test_same_session_window_rejects_session_end() -> None:
    frame = _bars(sessions=1, bars=10)
    result = make_targets(frame, [15])
    assert result["target_h15_valid"].eq(False).all()
    assert result["target_h15_mag_bps"].isna().all()


def test_forbidden_columns_are_absent_from_feature_matrix() -> None:
    frame = _bars(sessions=2)
    for column in FORBIDDEN_COLUMNS - {"datetime", "trade_date", "f_open", "f_high", "f_low", "f_close"}:
        frame[column] = 1.0
    features, manifest = build_prediction_features(frame)
    assert not set(features.columns) & FORBIDDEN_COLUMNS
    assert list(features.columns) == manifest


def test_opening_range_features_are_nan_before_0930() -> None:
    features, _ = build_prediction_features(_bars(sessions=1))
    before = _bars(sessions=1)["datetime"].dt.time < pd.Timestamp("09:30").time()
    assert features.loc[before, ["or_dist_high", "or_dist_low", "or_breakout_up", "or_breakout_dn"]].isna().all().all()


def test_fold_table_matches_frozen_spec_dates_exactly() -> None:
    assert fold_table() == [fold.as_dict() for fold in intraday_folds()]
    assert [(fold.train_end, fold.calibration_start, fold.oos_start, fold.oos_end) for fold in intraday_folds()] == [
        ("2022-06-23", "2022-07-01", "2022-10-01", "2022-12-31"),
        ("2022-12-24", "2023-01-01", "2023-04-01", "2023-06-30"),
        ("2023-06-23", "2023-07-01", "2023-10-01", "2023-12-31"),
        ("2023-12-24", "2024-01-01", "2024-04-01", "2024-06-30"),
    ]


def test_purge_removes_labels_crossing_embargo() -> None:
    rows = pd.DataFrame({"datetime": pd.to_datetime(["2022-06-23 14:00", "2022-06-23 23:50"]), "trade_date": pd.to_datetime(["2022-06-23", "2022-06-23"])})
    kept = purge_rows(rows, "2022-06-24", horizon=15)
    assert kept["datetime"].tolist() == [pd.Timestamp("2022-06-23 14:00")]


def test_cutoff_assertion_rejects_post_test_rows() -> None:
    with pytest.raises(AssertionError, match="sealed-test firewall"):
        assert_cutoff_safe(pd.DataFrame({"datetime": [pd.Timestamp("2025-07-01 09:15")]}))


def test_calibrator_is_monotone_and_small_input_uses_fallback() -> None:
    size = 2_500
    confidence = np.linspace(0.51, 0.99, size)
    probabilities = np.column_stack([(1 - confidence) * 0.6, (1 - confidence) * 0.4, confidence])
    truth = np.where(np.arange(size) % 3, "UP", "DOWN")
    sessions = np.repeat([f"s{index}" for index in range(25)], 100)
    calibrator = fit_correctness_calibrator(probabilities, truth, sessions)
    assert calibrator.method == "isotonic"
    calibrated = calibrate_confidence(calibrator, probabilities)
    assert np.diff(calibrated).min() >= -1e-12
    fallback = fit_correctness_calibrator(probabilities[:4], truth[:4], sessions[:4])
    assert fallback.method == "platt"


def test_confidence_ece_and_brier_known_fixture() -> None:
    result = confidence_metrics([1, 0, 1, 0], [0.9, 0.8, 0.6, 0.2], bins=10)
    assert result["brier"] == pytest.approx(0.2125)
    assert result["ece"] == pytest.approx(0.375)


def test_session_block_bootstrap_is_deterministic() -> None:
    values = np.arange(12, dtype=float)
    sessions = np.repeat(["a", "b", "c", "d"], 3)
    first = session_block_bootstrap(values, sessions, n_bootstrap=50, seed=20260718)
    second = session_block_bootstrap(values, sessions, n_bootstrap=50, seed=20260718)
    assert first == second


def test_smoke_runner_completes_on_synthetic_frame(tmp_path) -> None:
    result = run_experiment(_bars(sessions=6), tmp_path / "intraday-smoke", smoke=True, bootstrap=5)
    assert (tmp_path / "intraday-smoke" / "predictions_h15.parquet").exists()
    assert (tmp_path / "intraday-smoke" / "predictions_h60.parquet").exists()
    assert len(result["report"]) > 0
