"""Exactly twelve focused tests for the frozen intraday v1.1-smooth contract."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from contracts.intraday_targets_v1_1 import class_share_validity, make_targets
from evidence.intraday_eval_v1_1 import evaluate_predictions, paired_filter_bootstrap_ci
from features.intraday_ema_v1_1 import (
    EMA_FEATURE_COLUMNS,
    FEATURE_MANIFESTS,
    build_ema_features,
    build_ema_features_with_warmup,
    build_prediction_features_v1_1,
)
from models.intraday_predictor import fit_horizon_models as fit_v0_horizon_models
from models.intraday_predictor_v1_1 import fit_confidence_thresholds, fit_direction_model
from scripts.run_intraday_prediction_v1_1 import evaluate_scored, run_smoke


def _target_frame(minutes: int = 80, sessions: int = 1) -> pd.DataFrame:
    rows = []
    for day in range(sessions):
        start = pd.Timestamp("2025-06-02") + pd.offsets.BDay(day) + pd.Timedelta(hours=9, minutes=15)
        times = pd.date_range(start, periods=minutes, freq="min")
        close = 100.0 + np.arange(minutes, dtype=float) * 0.02
        for timestamp, value in zip(times, close, strict=True):
            rows.append({"datetime": timestamp, "trade_date": timestamp.normalize(), "f_close": value, "f_open": value, "f_high": value + 0.1, "f_low": value - 0.1, "realized_vol_30m": 0.0})
    return pd.DataFrame(rows)


def test_smooth_label_matches_hand_computed_fixture() -> None:
    frame = _target_frame(20)
    result = make_targets(frame, [15])
    alpha = 1.0 - 2.0 ** (-1.0 / 5.0)
    q = frame.loc[0, "f_close"]
    for value in frame.loc[1:15, "f_close"]:
        q = alpha * value + (1.0 - alpha) * q
    assert result.loc[0, "target_h15_smooth_return"] == pytest.approx(np.log(q / frame.loc[0, "f_close"]))
    assert result.loc[0, "target_h15_smooth_dir"] == "UP"


def test_smooth_label_window_excludes_prior_and_post_horizon_mutations() -> None:
    frame = _target_frame(20)
    baseline = make_targets(frame, [15])
    mutated = frame.copy()
    mutated.loc[0, "f_close"] = 999.0  # Q0/Ft is necessarily part of the registered formula.
    mutated.loc[17, "f_close"] = 999.0  # t+h+1 must not be read.
    mutated.loc[18, "f_close"] = 999.0  # later future bars must not be read.
    changed = make_targets(mutated, [15])
    assert changed.loc[1, "target_h15_smooth_return"] == baseline.loc[1, "target_h15_smooth_return"]
    assert changed.loc[1, "target_h15_smooth_dir"] == baseline.loc[1, "target_h15_smooth_dir"]


def test_class_share_validity_flags_structural_failure() -> None:
    valid = class_share_validity(["DOWN"] * 2 + ["FLAT"] * 3 + ["UP"] * 5)
    invalid = class_share_validity(["DOWN"] * 1 + ["FLAT"] * 1 + ["UP"] * 8)
    assert valid["valid"] is True
    assert invalid["valid"] is False
    assert invalid["structural_failure"] is True


def test_ema_features_are_causal_at_decision_timestamp() -> None:
    frame = _target_frame(70)
    clean = build_ema_features(frame)
    mutated = frame.copy()
    mutated.loc[56, "f_close"] += 1_000.0
    changed = build_ema_features(mutated)
    np.testing.assert_allclose(clean.loc[55, EMA_FEATURE_COLUMNS], changed.loc[55, EMA_FEATURE_COLUMNS], equal_nan=True)


def test_ema_features_reset_sessions_and_require_45_minute_warmup() -> None:
    frame = _target_frame(50, sessions=2)
    features = build_ema_features_with_warmup(frame)
    first_session = features.iloc[:50]
    second_session = features.iloc[50:]
    assert first_session.iloc[:45][EMA_FEATURE_COLUMNS].isna().all().all()
    assert second_session.iloc[:45][EMA_FEATURE_COLUMNS].isna().all().all()
    assert first_session.iloc[45][EMA_FEATURE_COLUMNS].notna().all()
    assert second_session.iloc[45][EMA_FEATURE_COLUMNS].notna().all()
    assert bool(features.loc[50, "ema_warmup"])


def test_s0_s3_manifests_are_exact() -> None:
    assert list(FEATURE_MANIFESTS) == ["S0", "S1", "S2", "S3"]
    assert len(FEATURE_MANIFESTS["S0"]) == 42
    assert FEATURE_MANIFESTS["S1"] == FEATURE_MANIFESTS["S0"] + EMA_FEATURE_COLUMNS
    assert FEATURE_MANIFESTS["S2"] == FEATURE_MANIFESTS["S0"]
    assert FEATURE_MANIFESTS["S3"] == FEATURE_MANIFESTS["S1"]
    _, columns = build_prediction_features_v1_1(_target_frame(70), "S3")
    assert columns == FEATURE_MANIFESTS["S3"]


def test_frozen_classifier_params_match_v0_architecture() -> None:
    rows = 12
    features = pd.DataFrame({"signal": np.linspace(-1, 1, rows)})
    targets = pd.DataFrame({"target_h15_dir": np.tile(["DOWN", "FLAT", "UP"], 4), "target_h15_mag_bps": np.ones(rows)})
    mask = np.ones(rows, dtype=bool)
    v0 = fit_v0_horizon_models(features, targets, mask, 15)
    v11_targets = targets.rename(columns={"target_h15_mag_bps": "target_h15_smooth_return_bps"}).copy()
    v11 = fit_direction_model(features, v11_targets, mask, 15, label_column="target_h15_dir")
    for key in ("n_estimators", "learning_rate", "num_leaves", "min_child_samples", "feature_fraction", "bagging_fraction", "reg_alpha", "reg_lambda", "deterministic"):
        assert v11.classifier.get_params()[key] == v0.classifier.get_params()[key]


def test_threshold_fitter_returns_fixed_calibration_percentiles_deterministically() -> None:
    confidence = np.linspace(0.1, 0.9, 101)
    first = fit_confidence_thresholds(confidence)
    second = fit_confidence_thresholds(confidence.copy())
    assert first == second
    assert first["primary"] == pytest.approx(np.percentile(confidence, 75))
    assert first["secondary"] == pytest.approx(np.percentile(confidence, 90))


def test_gate_table_is_mechanical_and_cost_legs_are_not_evaluable() -> None:
    rows = 100
    labels = np.tile(["DOWN", "FLAT", "UP"], rows // 3 + 1)[:rows]
    prediction = pd.DataFrame({
        "datetime": pd.date_range("2025-07-01 09:15", periods=rows, freq="min"),
        "trade_date": pd.date_range("2025-07-01", periods=rows, freq="min").normalize(),
        "p_down_raw": (labels == "DOWN").astype(float) * 0.9 + 0.05,
        "p_flat_raw": (labels == "FLAT").astype(float) * 0.9 + 0.05,
        "p_up_raw": (labels == "UP").astype(float) * 0.9 + 0.05,
        "direction": labels,
        "confidence": np.linspace(0.2, 0.99, rows),
        "realized_dir": labels,
        "realized_smooth_dir": labels,
        "target_h15_smooth_dir": labels,
    })
    result = evaluate_predictions(prediction, 15, s0_predictions=prediction, threshold=0.75, bootstrap_draws=20)
    assert result["gates"]["checks"]["class_share_validity"]["status"] == "PASS"
    assert result["gates"]["checks"]["net_mean_return_after_costs"]["status"] == "NOT_EVALUABLE"
    assert result["economic"]["status"] == "NOT_EVALUABLE"


def test_paired_bootstrap_improvement_ci_is_reproducible() -> None:
    rows = 120
    sessions = np.repeat(pd.date_range("2025-07-01", periods=6, freq="D").astype(str), 20)
    labels = np.tile(["DOWN", "UP"], rows // 2)
    prediction = pd.DataFrame({
        "trade_date": sessions,
        "direction": labels,
        "realized_dir": labels,
        "confidence": np.tile(np.linspace(0.2, 0.99, 20), 6),
        "p_down_raw": (labels == "DOWN").astype(float),
        "p_flat_raw": np.zeros(rows),
        "p_up_raw": (labels == "UP").astype(float),
    })
    first = paired_filter_bootstrap_ci(prediction, 0.75, horizon=15, draws=200, seed=20260718)
    second = paired_filter_bootstrap_ci(prediction, 0.75, horizon=15, draws=200, seed=20260718)
    assert first == second


def test_smoke_runs_are_deterministic() -> None:
    first = run_smoke(out_dir=None)
    second = run_smoke(out_dir=None)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["status"] == "SMOKE PASS"


def test_evaluate_refuses_fewer_than_60_sessions_without_descriptive(tmp_path) -> None:
    frame = _target_frame(70, sessions=3)
    scored = frame[["datetime", "trade_date"]].copy()
    for horizon in (15, 60):
        for column in (f"s3_h{horizon}_p_down_raw", f"s3_h{horizon}_p_flat_raw", f"s3_h{horizon}_p_up_raw", f"s3_h{horizon}_confidence"):
            scored[column] = 1 / 3
        scored[f"s3_h{horizon}_direction"] = "FLAT"
    with pytest.raises(ValueError, match="60 distinct sessions"):
        evaluate_scored(scored, tmp_path / "report")
    report = evaluate_scored(scored, tmp_path / "descriptive", descriptive=True)
    assert report["status"] == "RETROSPECTIVE/DESCRIPTIVE"
