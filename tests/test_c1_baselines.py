"""Synthetic tests for C1 activity-matched baselines."""
from __future__ import annotations

import json
from hashlib import sha256

import pandas as pd

from evidence.c1_replay import ReplayConfig
from models.c1_baselines import RANDOM_ENTRY_REPLICATES, activity_matched_baselines, random_entry_seed


def _fixture() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    days = ["2024-06-03", "2024-06-04", "2024-06-05", "2024-06-06"]
    times = ["09:45", "09:46", "10:00", "10:01"]
    rows = []
    for day_index, day in enumerate(days):
        for index, minute in enumerate(times):
            rows.append(
                {
                    "datetime": f"{day}T{minute}:00",
                    "f_open": 100.0,
                    "f_high": 100.1,
                    "f_low": 99.9,
                    "f_close": 100.0,
                    "realized_vol_30m": float(index + day_index),
                    "eligible": True,
                }
            )
    calendar = pd.DataFrame(
        {
            "trade_date": days,
            "front_expiry": ["2024-06-27"] * len(days),
            "front_instrument_id": [f"NIFTY-{day}" for day in days],
        }
    )
    candidate_trades = pd.DataFrame(
        {
            "decision_datetime": ["2024-06-03 09:45", "2024-06-04 09:45"],
            "score": [0.9, 0.8],
        }
    )
    return pd.DataFrame(rows), candidate_trades, calendar


def _build(replicates: int = 10):
    rows, candidate_trades, calendar = _fixture()
    return activity_matched_baselines(
        rows,
        candidate_trades,
        protocol_version="protocol-test-v1",
        candidate="candidate-a",
        fold="fold-1",
        sleeve_capital=1_000_000.0,
        contract_calendar=calendar,
        config=ReplayConfig(horizon_bars=1),
        random_replicates=replicates,
    )


def test_each_baseline_matches_synthetic_candidate_executed_count():
    baselines = _build()
    assert {name: result.executed_trade_count for name, result in baselines.items()} == {
        "time_of_day": 2,
        "volatility": 2,
        "unconditional_long": 2,
        "random_entry": 2,
    }
    assert baselines["time_of_day"].trades.decision_datetime.dt.strftime("%H:%M").tolist() == ["09:45", "09:45"]


def test_random_entry_is_seed_deterministic_and_replicates_differ():
    first = _build()["random_entry"]
    second = _build()["random_entry"]
    assert first.null_distribution == second.null_distribution
    trade_sets = {
        tuple(pd.to_datetime(entry["daily"][0]["trade_date"]).strftime("%Y-%m-%d") for _ in [0])
        for entry in first.null_distribution
    }
    assert len({json.dumps(entry["daily"], sort_keys=True) for entry in first.null_distribution}) > 1
    assert len(trade_sets) == 1


def test_random_null_distribution_persists_all_fast_test_replicates_and_default_is_1000():
    result = _build(replicates=10)["random_entry"]
    assert len(result.null_distribution) == 10
    assert RANDOM_ENTRY_REPLICATES == 1_000
    assert all(len(entry["daily"]) == 4 for entry in result.null_distribution)


def test_random_entry_seed_matches_registered_sha256_uint32_formula():
    expected = int.from_bytes(sha256(b"protocol-test-v1|candidate-a|fold-1|7").digest()[:4], "big")
    assert random_entry_seed("protocol-test-v1", "candidate-a", "fold-1", 7) == expected
