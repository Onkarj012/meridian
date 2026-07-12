from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from features.c2_sets import (
    ELIGIBILITY_COLUMNS,
    FEATURESET_CONFIG,
    KEY_COLUMNS,
    _vix_features,
    build_c2_matrices,
    c2p_config_hash,
    c2w_config_hash,
    derive_monthly_expiries,
    validate_expiry_derivation,
    iter_c2_matrices,
)


def _raw(sessions: int = 22) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for day_number, day in enumerate(pd.bdate_range("2024-01-02", periods=sessions)):
        start = day + pd.Timedelta(hours=9, minutes=15)
        previous_day_close = 100 + max(day_number - 1, 0) * 0.5 + 0.02 * 374
        for minute, timestamp in enumerate(pd.date_range(start, periods=375, freq="min")):
            # Alternation avoids the incumbent int8 streak overflow while
            # leaving deliberately hand-checkable OHLC values.
            close = 100 + day_number * 0.5 + minute * 0.02 + (0.01 if minute % 2 else 0.0)
            first_open = previous_day_close + 1.0 if minute == 0 else close - 0.01
            rows.append({
                "datetime": timestamp,
                "open": first_open,
                "high": close + 0.10,
                "low": close - 0.10,
                "close": close,
                "volume": 1_000 + day_number * 10 + minute,
                "oi": 100_000,
            })
    return pd.DataFrame(rows)


def test_registered_columns_and_determinism() -> None:
    raw = _raw()
    c2w, c2p = build_c2_matrices(raw)
    assert c2w.columns.tolist() == KEY_COLUMNS + FEATURESET_CONFIG["c2w"]["columns"] + ELIGIBILITY_COLUMNS
    assert c2p.columns.tolist() == KEY_COLUMNS + FEATURESET_CONFIG["c2p"]["columns"] + ELIGIBILITY_COLUMNS
    assert len(FEATURESET_CONFIG["c2w"]["columns"]) == 33
    assert len(FEATURESET_CONFIG["c2p"]["columns"]) == 23
    again_w, again_p = build_c2_matrices(raw.copy())
    assert_frame_equal(c2w, again_w)
    assert_frame_equal(c2p, again_p)


def test_batch_and_iterator_paths_are_session_exact():
    raw = _raw(3)
    batch_w, batch_p = build_c2_matrices(raw)
    iterator = list(iter_c2_matrices(raw))
    iter_w = pd.concat([pair[0] for pair in iterator], ignore_index=True)
    iter_p = pd.concat([pair[1] for pair in iterator], ignore_index=True)
    assert_frame_equal(batch_w, iter_w, check_exact=True, check_names=True)
    assert_frame_equal(batch_p, iter_p, check_exact=True, check_names=True)


def test_c2_builders_reject_timezone_aware_timestamps():
    raw = _raw(1).copy()
    raw["datetime"] = raw["datetime"].dt.tz_localize("Asia/Kolkata")
    with pytest.raises(ValueError, match="naive IST"):
        build_c2_matrices(raw)


def test_iterator_requires_strict_session_order():
    raw = _raw(3)
    days = [frame for _, frame in raw.groupby(raw["datetime"].dt.normalize(), sort=True)]
    with pytest.raises(ValueError, match="strictly increasing"):
        list(iter_c2_matrices([days[1], days[0], days[2]], trading_dates=pd.bdate_range("2024-01-02", periods=3)))


def test_trailing_standardizers_never_use_future_sessions() -> None:
    raw = _raw()
    original_w, original_p = build_c2_matrices(raw)
    mutated = raw.copy()
    final_session = mutated["datetime"].dt.normalize().max()
    final = mutated["datetime"].dt.normalize() == final_session
    mutated.loc[final, ["close", "high", "low", "volume"]] *= 3
    changed_w, changed_p = build_c2_matrices(mutated)
    before = original_w["session_date"] < final_session
    # These are every trailing-60 field across both registered feature sets.
    assert_frame_equal(
        original_w.loc[before, ["volume_surprise_60d"]].reset_index(drop=True),
        changed_w.loc[before, ["volume_surprise_60d"]].reset_index(drop=True),
    )
    for column in ("vwap_dev_z60", "opening_range_width_z60", "range_expansion_15m", "range_expansion_30m"):
        assert_frame_equal(
            original_p.loc[before, [column]].reset_index(drop=True),
            changed_p.loc[before, [column]].reset_index(drop=True),
        )


def test_vix_is_strictly_t_minus_one() -> None:
    sessions = pd.bdate_range("2024-01-02", periods=260)
    vix = pd.DataFrame({"date": sessions, "close": np.arange(1, 261, dtype=float) + 10})
    result = _vix_features(sessions, sessions, vix)
    day = sessions[-1]
    expected_return = math.log(vix.iloc[-2]["close"] / vix.iloc[-3]["close"])
    assert result.loc[day, "vix_t1_return"] == expected_return
    altered = vix.copy()
    altered.loc[altered["date"] == day, "close"] = 9_999.0
    changed = _vix_features(sessions, sessions, altered)
    assert result.loc[day, "vix_t1_return"] == changed.loc[day, "vix_t1_return"]
    assert result.loc[day, "vix_t1_z252"] == changed.loc[day, "vix_t1_z252"]


def test_expiry_derivation_and_validation() -> None:
    dates = pd.bdate_range("2024-01-01", "2025-10-31")
    derived = derive_monthly_expiries(dates)
    january = derived.loc[derived["month"] == "2024-01", "derived_expiry"].iloc[0]
    october = derived.loc[derived["month"] == "2025-10", "derived_expiry"].iloc[0]
    assert january == pd.Timestamp("2024-01-25")  # last Thursday
    assert october == pd.Timestamp("2025-10-28")  # last Tuesday after the rule change
    known = derived.rename(columns={"derived_expiry": "expiry"})[["expiry"]]
    report = validate_expiry_derivation(dates, known)
    assert report["overlap_count"] == len(known)
    assert report["match_rate"] == 1.0


def test_pinned_path_formulae() -> None:
    c2w, c2p = build_c2_matrices(_raw())
    final_day = c2p["session_date"].max()
    # At 09:30 the 09:15..09:29 opening range is complete.  The fixture gives
    # OR low=C0-.10, OR high=C14+.10 and C15, so location=(.41/.48).
    row = c2p[(c2p["session_date"] == final_day) & (c2p["datetime"].dt.time == pd.Timestamp("09:30").time())].iloc[0]
    assert np.isclose(row["opening_range_location"], 0.41 / 0.48)
    # Linear close drift gives |C_t-C_t-15| / sum(|dC|) exactly one.
    row = c2p[(c2p["session_date"] == final_day) & (c2p["datetime"].dt.time == pd.Timestamp("10:00").time())].iloc[0]
    assert np.isclose(row["trend_efficiency_15m"], 1.0)
    # First open is deliberately one point above the prior close; this fixture
    # has fully retraced that gap by 10:00, so clipping pins the value to one.
    assert row["gap_fill_fraction"] == 1.0
    progress = ((10 * 60 - 555) / 375)
    assert np.isclose(row["session_time_sin"], math.sin(2 * math.pi * progress))
    assert np.isclose(row["session_time_cos"], math.cos(2 * math.pi * progress))


def test_config_hash_pins() -> None:
    assert c2w_config_hash() == "ebd88e3b7ff8a2186c1754c6870a8aea13d50ee8029046f00ce55a4ddcfcef7e"
    assert c2p_config_hash() == "a12095748fda2d088ff7340c12b266c894760c1827ca53f5bffe102716c8c92b"
