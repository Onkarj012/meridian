from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from features.sleeve_f_router import FUTURES_FEATURES
from models.c1_candidates import (
    A_PARAMS,
    C_PARAMS,
    b_scores,
    calibrate_b,
    c_trade_mask,
    derive_seed,
    train_c,
    write_b_artifact,
)


def test_a_params_match_registered_router() -> None:
    assert A_PARAMS == {
        "objective": "binary",
        "n_estimators": 500,
        "learning_rate": 0.05,
        "num_leaves": 63,
        "min_child_samples": 200,
        "feature_fraction": 0.85,
        "bagging_fraction": 0.85,
        "bagging_freq": 5,
        "reg_alpha": 0.1,
        "reg_lambda": 1.0,
        "is_unbalance": True,
    }


def test_c_params_match_registered_huber_challenger() -> None:
    assert C_PARAMS == {
        "objective": "huber",
        "n_estimators": 200,
        "max_depth": 2,
        "num_leaves": 4,
        "learning_rate": 0.03,
        "min_child_samples": 100,
        "feature_fraction": 0.7,
        "bagging_fraction": 0.7,
        "reg_alpha": 1.0,
        "reg_lambda": 10.0,
    }


def test_c_trades_only_for_positive_predictions_and_applies_floor() -> None:
    predictions = np.array([-0.2, 0.0, 1e-12, 0.2, 0.4, np.nan])

    assert c_trade_mask(predictions, -0.5).tolist() == [False, False, True, True, True, False]
    assert c_trade_mask(predictions, 0.3).tolist() == [False, False, False, False, True, False]


def test_seed_derivation_is_stable_uint32_and_distinct() -> None:
    first = derive_seed("protocol-v1", "A", "fold_1")
    assert first == derive_seed("protocol-v1", "A", "fold_1")
    assert 0 <= first <= 2**32 - 1
    assert len({first, derive_seed("protocol-v1", "C", "fold_1"), derive_seed("protocol-v1", "A", "fold_2")}) == 3


def test_c_training_is_deterministic_for_same_inputs() -> None:
    rng = np.random.default_rng(7)
    train = pd.DataFrame(rng.normal(size=(24, len(FUTURES_FEATURES))), columns=FUTURES_FEATURES)
    train["net_return_r"] = np.linspace(-1.0, 1.0, len(train))
    validation = train.iloc[-8:].copy()
    training = train.iloc[:-8].copy()

    first = train_c(training, validation, fold="fold_1")
    second = train_c(training, validation, fold="fold_1")

    np.testing.assert_array_equal(first.model.predict(validation[FUTURES_FEATURES]), second.model.predict(validation[FUTURES_FEATURES]))
    assert first.metadata == second.metadata


def test_b_calibrator_freezes_constants_and_rejects_post_2020_data(tmp_path) -> None:
    rows = pd.DataFrame({
        "trade_date": ["2020-01-02", "2020-01-02", "2020-01-03", "2020-01-03", "2020-01-04", "2020-01-04"],
        "realized_vol_30m": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        "minute_of_day": [570, 571, 570, 571, 570, 571],
    })
    windows = [[570, 572]]

    artifact = calibrate_b(rows, windows=windows)
    output = tmp_path / "b_constants.json"
    write_b_artifact(artifact, output)

    assert json.loads(output.read_text()) == artifact
    assert artifact["tercile"] == 2.0 / 3.0
    assert artifact["trailing_sessions"] == 60
    assert artifact["windows"] == windows
    assert np.isnan(b_scores(rows, artifact)[0])
    assert np.isfinite(b_scores(rows, artifact)[-1])

    with pytest.raises(ValueError, match="strictly 2020"):
        calibrate_b(pd.concat([
            rows,
            pd.DataFrame({"trade_date": ["2021-01-01"], "realized_vol_30m": [7.0], "minute_of_day": [570]}),
        ], ignore_index=True), windows=windows)
