"""Exactly one focused test for the V1 model contract."""
from __future__ import annotations

import numpy as np
import pandas as pd

from models.intraday_predictor_v1 import fit_horizon_models, predict_horizon


def test_seeded_fit_is_deterministic_and_predicts_nonnegative_median_move() -> None:
    rows = 24
    features = pd.DataFrame({
        "signal": np.linspace(-1.0, 1.0, rows),
        "noise": np.arange(rows, dtype=float) % 4,
    })
    targets = pd.DataFrame({
        "target_h15_dir": np.tile(["DOWN", "FLAT", "UP"], rows // 3),
        "target_h15_normalized_magnitude": np.linspace(0.1, 1.2, rows),
        "target_h15_scale": np.full(rows, 5.0),
    })
    mask = np.ones(rows, dtype=bool)
    first = fit_horizon_models(features, targets, mask, 15)
    second = fit_horizon_models(features, targets, mask, 15)
    first_prediction = predict_horizon(first, features, targets=targets)
    second_prediction = predict_horizon(second, features, targets=targets)
    pd.testing.assert_frame_equal(first_prediction, second_prediction)
    assert first_prediction["median_abs_move_bps"].ge(0).all()
    assert "median_abs_move_bps" in first_prediction
    assert "expected_log_return_bps" not in first_prediction
    assert not hasattr(first, "expected_log_return_bps")
    assert first.regressor.get_params()["objective"] == "quantile"
    assert first.regressor.get_params()["alpha"] == 0.50
