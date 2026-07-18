"""Exactly three focused tests for the V1 target contract."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from contracts.intraday_targets import make_targets as make_targets_v0
from contracts.intraday_targets_v1 import make_targets


def _rows(timestamps, closes, volatility=0.0) -> pd.DataFrame:
    return pd.DataFrame({
        "datetime": pd.to_datetime(timestamps),
        "f_close": closes,
        "realized_vol_30m": volatility,
    })


def test_v1_direction_labels_match_v0_exactly() -> None:
    timestamps = pd.date_range("2025-06-02 09:15", periods=61, freq="min")
    closes = 100 + np.linspace(0, 1, len(timestamps))
    frame = _rows(timestamps, closes, volatility=0.2)
    v0 = make_targets_v0(frame, [15, 60])
    v1 = make_targets(frame, [15, 60])
    for horizon in (15, 60):
        assert v1[f"target_h{horizon}_dir"].astype("string").tolist() == v0[f"target_h{horizon}_dir"].astype("string").tolist()
        assert v1[f"target_h{horizon}_valid"].tolist() == v0[f"target_h{horizon}_valid"].tolist()


def test_v1_magnitude_columns_follow_return_scale_and_floor() -> None:
    timestamps = pd.date_range("2025-06-02 09:15", periods=3, freq="min")
    frame = _rows(timestamps, [100.0, 100.02, 100.0], volatility=[0.0, 1.0, 0.0])
    result = make_targets(frame, [1])
    expected_return = 10_000 * np.log(100.02 / 100.0)
    assert result.loc[0, "target_h1_return_bps"] == pytest.approx(expected_return)
    assert result.loc[0, "target_h1_scale"] == pytest.approx(2.0)
    assert result.loc[0, "target_h1_normalized_magnitude"] == pytest.approx(abs(expected_return) / 2.0)
    sigma_h = 1.0 / np.sqrt(252 * 375)
    expected_scale = 10_000 * sigma_h
    assert result.loc[1, "target_h1_scale"] == pytest.approx(expected_scale)
    assert result.loc[1, "target_h1_normalized_magnitude"] == pytest.approx(abs(10_000 * np.log(100.0 / 100.02)) / expected_scale)


def test_v1_rejects_missing_and_cross_session_future_timestamps_without_dropping_rows() -> None:
    frame = _rows(["2025-06-02 23:59", "2025-06-03 00:00", "2025-06-03 00:01", "2025-06-04 09:15"], [100.0, 101.0, 101.5, 102.0])
    result = make_targets(frame, [1])
    assert len(result) == 4
    assert result["target_h1_valid"].tolist() == [False, True, False, False]
    assert pd.isna(result.loc[0, "target_h1_return_bps"])
    assert pd.isna(result.loc[2, "target_h1_normalized_magnitude"])
