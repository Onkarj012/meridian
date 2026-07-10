from __future__ import annotations

import math
from datetime import date

import numpy as np
import pandas as pd
import pytest

from features.sleeve_f_router import FUTURES_FEATURES, add_regime, compute_features


def _bars() -> pd.DataFrame:
    times = pd.date_range("2025-01-06 09:15", periods=65, freq="min")
    close = np.arange(100.0, 165.0)
    return pd.DataFrame({"datetime": times, "f_open": close - 0.25, "f_high": close + 1, "f_low": close - 1, "f_close": close, "f_vol": np.arange(10.0, 75.0), "f_oi": np.arange(1000.0, 1065.0), "s_close": close - 0.5})


def test_feature_registry_and_hand_computed_values():
    result = compute_features(_bars(), date(2025, 1, 6))
    row = result.iloc[60]
    assert len(FUTURES_FEATURES) == 39
    assert row["ret_60m"] == pytest.approx(0.6)
    assert row["log_ret_5m"] == math.log(160 / 155)
    assert row["atr_5m"] == 2.0
    assert row["or_dist_high"] == (160 - 115) / (115 - 99)
    assert row["or_breakout_up"] == 1
    assert row["oi_chg_5m"] == 5 / 1055
    assert row["basis"] == 0.5 / 159.5
    assert row["minute_of_day"] == 10 * 60 + 15
    assert row["session_progress"] == (615 - 555) / 375
    assert row["consec_bars"] == 60


def test_regime_precedence_matches_incumbent():
    frame = pd.DataFrame({"realized_vol_30m": [1.0, 2.0, 3.0, 4.0], "ema_slope": [0.0, 0.0, 0.0, 0.002], "ret_30m": [0.0, 0.0, 0.0, 0.01]})
    result = add_regime(frame)
    assert result.loc[0, "regime"] == "compression"
    assert result.loc[3, "regime"] == "expansion"
