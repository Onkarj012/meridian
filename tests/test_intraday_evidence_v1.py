from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from evidence.intraday_folds_v1 import (
    apply_fold, assert_development_safe, fold_table, intraday_folds,
)
from evidence.intraday_metrics_v1 import (
    development_gate_verdicts, fixed_magnitude_baselines, magnitude_metrics,
)


def test_protocol_folds_embargo_purge_and_development_firewall() -> None:
    with open("registrations/intraday-pred-v1/evaluation_protocol.json", encoding="utf-8") as handle:
        protocol = json.load(handle)
    expected = protocol["development_folds"]
    actual = fold_table()
    assert len(actual) == 5
    for row, source in zip(actual, expected, strict=True):
        assert row["name"] == f"fold_{source['fold']}"
        assert row["train_start"] == source["train"].split("/")[0]
        assert row["train_end"] == source["train"].split("/")[1]
        assert row["calibration_start"] == source["calibration"].split("/")[0]
        assert row["oos_end"] == source["oos"].split("/")[1]
        assert row["embargo_1_start"] == source["embargoes"][0].split("/")[0]
        assert row["embargo_2_start"] == source["embargoes"][1].split("/")[0]

    frame = pd.DataFrame({
        "datetime": pd.to_datetime(["2022-06-23 15:00", "2022-06-23 15:20", "2022-09-23 15:20", "2022-10-01 09:30"]),
        "trade_date": pd.to_datetime(["2022-06-23", "2022-06-23", "2022-09-23", "2022-10-01"]),
        "target_h15_label_end": pd.to_datetime(["2022-06-23 15:15", "2022-06-24 15:35", "2022-09-24 15:35", "2022-10-01 09:45"]),
    })
    split = apply_fold(frame, intraday_folds()[0], 15)
    assert len(split["train"]) == 1
    assert len(split["calibration"]) == 0
    assert len(split["oos"]) == 1
    with pytest.raises(AssertionError, match="development firewall"):
        assert_development_safe(pd.DataFrame({"datetime": [pd.Timestamp("2025-07-01 09:15")] }))


def test_magnitude_skill_gate_verdict_and_time_bucket_fallback() -> None:
    timestamps = pd.to_datetime(["2024-10-01 09:30", "2024-10-01 10:00", "2024-10-01 10:30", "2024-10-01 11:00"])
    actual = np.array([10.0, 20.0, 30.0, 40.0])
    model = np.array([9.0, 21.0, 31.0, 39.0])
    metrics = magnitude_metrics(actual, model, timestamps=timestamps, baseline_predictions={"zero": np.zeros(4)}, bootstrap_draws=5)
    assert metrics["mae_bps"] == pytest.approx(1.0)
    assert metrics["relative_mae_skill"] == pytest.approx(0.96)
    gates = development_gate_verdicts({
        "direction": {"pooled_balanced_accuracy": 0.40, "pooled_macro_auc": 0.60, "fold_balanced_accuracies": [0.40] * 5},
        "confidence": {"ece": 0.02, "decile_accuracy_spearman": 0.90, "top_minus_bottom_decile_accuracy": 0.10},
        "magnitude": {"relative_mae_skill": 0.03, "positive_skill_folds": 4, "daily_spearman_ic": {"mean": 0.06, "positive_fraction": 0.60}, "non_overlapping_mae_skill": 0.02},
    }, horizon=15)
    assert gates["status"] == "passed"

    train = pd.DataFrame({
        "datetime": pd.to_datetime(["2024-10-01 09:30", "2024-10-01 10:00"]),
        "target_h15_return_bps": [20.0, 40.0],
        "target_h15_normalized_magnitude": [2.0, 4.0],
        "target_h15_scale": [10.0, 10.0],
    })
    evaluation = pd.DataFrame({
        "datetime": pd.to_datetime(["2024-10-02 09:30", "2024-10-02 11:00"]),
        "target_h15_scale": [10.0, 10.0],
    })
    baselines = fixed_magnitude_baselines(train, evaluation, 15)
    assert baselines["time_bucket_30m_train_median"].tolist() == pytest.approx([20.0, 30.0])
