from __future__ import annotations

from datetime import date, timedelta
import inspect

import pandas as pd
import pytest

from policy.activity_band import (
    BAND_CONFIG_HASH,
    ActivityBand,
    band_registration_record,
    check_activity,
    derive_activity_band,
    derive_activity_band_pooled,
)


def _ledger(counts: list[int], start: date = date(2025, 1, 1)) -> dict[date, int]:
    return {start + timedelta(days=index): count for index, count in enumerate(counts)}


def test_known_synthetic_distribution_has_hand_computed_exact_band():
    # 160 sessions yields 101 windows: totals 0..59 once, then 60 forty-one times.
    band = derive_activity_band(_ledger([0] * 60 + [1] * 100))

    assert (band.floor, band.ceil, band.window, band.n_windows) == (0, 60, 60, 101)


# Fixed, precomputed Binomial(3, 0.2333)-ish draws; never use random at test runtime.
BINOMIALISH_60_COUNTS = [
    1, 0, 2, 1, 0, 1, 1, 0, 0, 2, 1, 0, 1, 0, 1, 2, 0, 1, 0, 0,
    1, 1, 0, 2, 0, 1, 1, 0, 1, 0, 2, 0, 1, 1, 0, 1, 0, 2, 1, 0,
    1, 0, 1, 1, 0, 2, 0, 1, 0, 1, 1, 0, 2, 0, 1, 0, 1, 1, 0, 2,
]


def test_binomialish_sanity_anchor_is_in_spec_neighborhood():
    # One 60-session replay block is repeated as 100 separate folds to satisfy
    # the registered minimum.  The 31--53 interval is deliberately loose:
    # it is only the §3.5 independence-based sanity anchor, not the procedure.
    band = derive_activity_band_pooled([_ledger(BINOMIALISH_60_COUNTS)] * 100)

    assert 31 <= band.floor <= band.ceil <= 53
    assert (band.floor, band.ceil) == (44, 44)


def test_pooled_windows_do_not_cross_candidate_or_fold_seams():
    zero_ledger = _ledger([0] * 140)
    three_ledger = _ledger([3] * 140, start=date(2026, 1, 1))

    band = derive_activity_band_pooled([zero_ledger, three_ledger])

    # Each ledger contributes 81 windows; concatenation would incorrectly give 221.
    assert (band.n_windows, band.floor, band.ceil) == (162, 0, 180)


def test_short_series_contributes_no_windows_and_total_under_100_raises():
    complete_blocks = [_ledger([1] * 60, start=date(2025, 1, 1) + timedelta(days=100 * index)) for index in range(99)]
    short_block = _ledger([1] * 59, start=date(2040, 1, 1))

    with pytest.raises(ValueError, match=r"need at least 100 rolling 60-session windows, got 99"):
        derive_activity_band_pooled([*complete_blocks, short_block])


def test_registration_record_hash_is_stable_and_pinned():
    counts = [0] * 60 + [1] * 100
    windows = list(range(60)) + [60] * 41
    record = band_registration_record(derive_activity_band(_ledger(counts)), windows)

    assert record["registration_hash"] == "036060542ee2aea9e0415b980c9c2fd5effc5ecd73b178565b62ed114eda5ce6"
    assert record["config_hash"] == BAND_CONFIG_HASH
    assert tuple(sorted(record.items())) == tuple(sorted(band_registration_record(derive_activity_band(_ledger(counts)), windows).items()))


def test_derivation_is_deterministic_integer_bounded_and_count_only():
    series = pd.Series([0] * 60 + [1] * 100, index=pd.date_range("2025-01-01", periods=160))

    first = derive_activity_band(series)
    second = derive_activity_band(series)

    assert first == second
    assert isinstance(first.floor, int)
    assert isinstance(first.ceil, int)
    assert {"pnl", "label", "labels"}.isdisjoint(inspect.signature(derive_activity_band).parameters)


def test_check_activity_single_window_breach_is_investigate_only():
    band = ActivityBand(31, 53, 60, 100, "numpy.percentile(method='linear')", "ledger", BAND_CONFIG_HASH)

    assert check_activity(band, 31) == "ok"
    assert check_activity(band, 53) == "ok"
    assert check_activity(band, 30) == "investigate"
    assert check_activity(band, 54) == "investigate"
