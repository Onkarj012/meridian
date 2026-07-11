from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from features.repaired import (
    REPAIR_CONFIG,
    build_minute_volume_baselines,
    compute_repaired_features,
    repaired_features_multi,
    repair_config_hash,
)
from features.sleeve_f_router import compute_features


def _session(day: str, closes: list[float], *, volumes: list[float] | None = None, oi: list[float] | None = None) -> pd.DataFrame:
    times = pd.date_range(f"{day} 09:15", periods=len(closes), freq="min")
    return pd.DataFrame({
        "date": times,
        "open": closes,
        "high": np.asarray(closes) + 1,
        "low": np.asarray(closes) - 1,
        "close": closes,
        "f_vol": volumes if volumes is not None else [100.0] * len(closes),
        "f_oi": oi if oi is not None else [1000.0] * len(closes),
        "s_close": closes,
    })


def test_true_gap_uses_explicit_previous_session_last_close() -> None:
    first = _session("2024-01-02", [100.0, 105.0])
    second = _session("2024-01-03", [110.0, 111.0])

    no_prior = compute_repaired_features(first, pd.Timestamp("2024-01-02").date(), None, None)
    repaired = compute_repaired_features(second, pd.Timestamp("2024-01-03").date(), 105.0, None)

    assert no_prior["true_gap_pct"].isna().all()
    assert np.allclose(repaired["true_gap_pct"], 5 / 105)
    assert repaired["gap_pct"].eq(0.0).all()  # incumbent parity is retained


def test_natr_is_atr_divided_by_close() -> None:
    day = _session("2024-01-02", [100.0, 101.0, 102.0, 103.0, 104.0])
    repaired = compute_repaired_features(day, pd.Timestamp("2024-01-02").date(), None, None)
    row = repaired.iloc[4]

    assert row["natr_5m"] == pytest.approx(row["atr_5m"] / row["f_close"])


def test_equal_close_resets_repaired_streak_but_not_incumbent() -> None:
    day = _session("2024-01-02", [100.0, 101.0, 102.0, 102.0, 103.0])
    repaired = compute_repaired_features(day, pd.Timestamp("2024-01-02").date(), None, None)
    incumbent_day = day.rename(columns={"open": "f_open", "high": "f_high", "low": "f_low", "close": "f_close"}).copy()
    incumbent_day["datetime"] = incumbent_day["date"]
    incumbent = compute_features(
        incumbent_day,
        pd.Timestamp("2024-01-02").date(),
    )

    assert incumbent["consec_bars"].iloc[3] == -1
    assert repaired["consec_bars"].iloc[3] == -1
    assert repaired["repaired_consec_bars"].iloc[3] == 0
    assert repaired["repaired_consec_bars"].iloc[4] == 1


def test_volume_surprise_is_causal_minute_matched_and_requires_twenty_sessions() -> None:
    sessions = []
    for offset in range(22):
        day = (pd.Timestamp("2024-01-02") + pd.offsets.BDay(offset)).strftime("%Y-%m-%d")
        sessions.append(_session(day, [100.0, 101.0], volumes=[10.0, 100.0]))
    raw = pd.concat(sessions, ignore_index=True)
    baselines = build_minute_volume_baselines(raw)
    target_day = pd.Timestamp(raw["date"].dt.normalize().unique()[20])
    target = raw[raw["date"].dt.normalize() == target_day]
    result = compute_repaired_features(target, target_day.date(), 101.0, baselines)

    assert result["volume_surprise_60d"].iloc[0] == pytest.approx(0.0)
    assert result["volume_surprise_60d"].iloc[1] == pytest.approx(0.0)
    early = baselines[baselines["trade_date"] == pd.Timestamp(raw["date"].dt.normalize().unique()[19])]
    assert early["volume_median_baseline_60d"].isna().all()

    mutated = raw.copy()
    mutated.loc[mutated["date"].dt.normalize() > target_day, "f_vol"] = 999999.0
    mutated_baselines = build_minute_volume_baselines(mutated)
    pd.testing.assert_frame_equal(
        baselines[baselines["trade_date"] == target_day].reset_index(drop=True),
        mutated_baselines[mutated_baselines["trade_date"] == target_day].reset_index(drop=True),
    )


def test_repaired_oi_quadrants_zero_change_is_all_zero_and_nonzero_matches_incumbent() -> None:
    closes = [100, 101, 102, 103, 104, 105, 105, 106, 107, 108, 105, 110]
    oi = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 106]
    day = _session("2024-01-02", closes, oi=oi)
    repaired = compute_repaired_features(day, pd.Timestamp("2024-01-02").date(), None, None)

    repaired_flags = [
        "repaired_oi_long_buildup", "repaired_oi_short_buildup",
        "repaired_oi_short_cover", "repaired_oi_long_unwind",
    ]
    # Row 10 has zero price change over five bars; row 11 has zero OI change.
    assert repaired.loc[10, repaired_flags].eq(0).all()
    assert repaired.loc[11, repaired_flags].eq(0).all()
    cases = [
        ("long_buildup", [100, 101, 102, 103, 104, 105], [100, 101, 102, 103, 104, 105]),
        ("short_buildup", [105, 104, 103, 102, 101, 100], [100, 101, 102, 103, 104, 105]),
        ("short_cover", [100, 101, 102, 103, 104, 105], [105, 104, 103, 102, 101, 100]),
        ("long_unwind", [105, 104, 103, 102, 101, 100], [105, 104, 103, 102, 101, 100]),
    ]
    for flag, prices, oi_values in cases:
        nonzero = compute_repaired_features(
            _session("2024-01-03", prices, oi=oi_values), pd.Timestamp("2024-01-03").date(), None, None
        )
        assert nonzero.loc[5, f"repaired_oi_{flag}"] == nonzero.loc[5, f"oi_{flag}"] == 1


def test_config_hash_and_multi_session_driver_are_deterministic() -> None:
    canonical = json.dumps(REPAIR_CONFIG, sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert repair_config_hash() == "5936ef8d937ffc8a67f575f7f57d10b671798f8c43315e7ccafab24e9e5495bf"
    assert repair_config_hash() == hashlib.sha256(canonical.encode()).hexdigest()

    raw = pd.concat([_session("2024-01-02", [100, 101]), _session("2024-01-03", [102, 103])], ignore_index=True)
    pd.testing.assert_frame_equal(repaired_features_multi(raw), repaired_features_multi(raw.sample(frac=1, random_state=7)))
