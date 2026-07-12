from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from evidence.sleeve_f_router_replay import ReplayConfig, replay, simulate_causal_trade, simulate_legacy_trade
from features.sleeve_f_router import FUTURES_FEATURES


class IncreasingModel:
    def predict(self, frame):
        return np.arange(len(frame), dtype=float)


class DecreasingModel:
    def predict(self, frame):
        return -np.arange(len(frame), dtype=float)


def _features(rows: int = 80) -> pd.DataFrame:
    timestamps = pd.date_range("2025-01-06 09:45", periods=rows, freq="min")
    frame = pd.DataFrame({"datetime": timestamps, "trade_date": timestamps.normalize(), "minute_of_day": timestamps.hour * 60 + timestamps.minute, "regime": "range", "f_open": 100.0, "f_high": 100.0, "f_low": 100.0, "f_close": 100.0})
    for feature in FUTURES_FEATURES:
        if feature not in frame:
            frame[feature] = 0.0
    return frame


def test_replay_enforces_three_trade_daily_cap_and_legacy_metadata():
    trades, metadata = replay(_features(), IncreasingModel())
    assert len(trades) == 3
    assert metadata["lookahead"] is True
    assert metadata["selection_lookahead"] is True
    assert metadata["residual_lookahead"] == ["regime_eligibility"]
    assert metadata["promotable"] is False


def test_causal_metadata_declares_regime_eligibility_residual_lookahead():
    _, metadata = replay(_features(), IncreasingModel(), variant="causal")
    assert metadata["lookahead"] is True
    assert metadata["selection_lookahead"] is False
    assert metadata["regime_source"] == "add_regime (noncausal full-frame quantiles)"
    assert metadata["residual_lookahead"] == ["regime_eligibility"]


def test_daily_halt_blocks_later_candidates():
    frame = _features()
    frame.loc[1:, "f_close"] = 99.0
    config = replace(ReplayConfig(), daily_halt=-100.0)
    trades, _ = replay(frame, DecreasingModel(), config=config)
    assert len(trades) == 1
    assert trades.iloc[0]["exit_reason"] == "STOP"


def test_close_only_target_and_stop_floor():
    bars = _features(3)
    bars.loc[1, "f_close"] = 101.0
    result = simulate_legacy_trade(0, bars, 100.0, 1.0, ReplayConfig())
    assert result["exit_reason"] == "TARGET"
    assert result["exit_px"] == 100.4
    bars.loc[1, "f_close"] = 0.0
    result = simulate_legacy_trade(0, bars, 100.0, 1.0, ReplayConfig(stop_pct=1.0))
    assert result["net_pnl_inr"] == -3000.0


def test_causal_double_touch_is_conservative_stop():
    bars = _features(2)
    bars.loc[0, ["f_open", "f_high", "f_low"]] = [100.0, 101.0, 99.0]
    result = simulate_causal_trade(0, bars, 1.0, ReplayConfig(variant="causal"))
    assert result["exit_reason"] == "STOP"
