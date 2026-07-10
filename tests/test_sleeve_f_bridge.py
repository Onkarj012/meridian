"""Slow, pre-declared bridge gate for the original router ledger."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from evidence.sleeve_f_router_replay import load_router_model, replay, summarize
from features.sleeve_f_router import build_proxy_features
from scripts.run_sleeve_f_bridge import (
    EXIT_COUNT_TOLERANCE,
    NET_PNL_TOLERANCE,
    TRADE_COUNT_TOLERANCE,
    WINDOW_END,
    WINDOW_START,
    compare,
)

SOURCE_ROOT = Path("/Users/onkarj012/Projects/market/intranet_optinet")


@pytest.mark.slow
def test_legacy_parity_bridge_within_predeclared_tolerance():
    if not SOURCE_ROOT.exists():
        pytest.skip("incumbent source checkout is unavailable")
    raw = pd.read_csv(SOURCE_ROOT / "data/nifty_intraday/NIFTY 50_minute.csv")
    timestamps = pd.to_datetime(raw["date"])
    raw = raw[(timestamps >= WINDOW_START) & (timestamps <= WINDOW_END)]
    features = build_proxy_features(raw)
    actual, metadata = replay(features, load_router_model(), variant="legacy_parity")
    reference = pd.read_parquet(SOURCE_ROOT / "results/router_v0/phase3_fwd_no_guard.parquet")
    result = compare(summarize(actual), summarize(reference))
    assert metadata["lookahead"] is True
    assert TRADE_COUNT_TOLERANCE == 0.01
    assert NET_PNL_TOLERANCE == 0.01
    assert EXIT_COUNT_TOLERANCE == 0.02
    assert result["passed"], result
