"""Synthetic tests for C1 metrics and MBB evidence."""
from __future__ import annotations

import math

import pandas as pd
import pytest

from evidence import stats
from evidence.c1_metrics import (
    annualized_sharpe,
    mbb_ci,
    paired_difference_mbb,
    score_quintile_monotonicity,
    stitch_oos_daily,
)


def _daily(days: list[str], values: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"trade_date": days, "policy_return_bps": values, "trade_count": [1] * len(days)})


def test_stitching_rejects_overlapping_strict_oos_folds():
    first = _daily(["2024-01-02", "2024-01-03"], [1.0, 2.0])
    second = _daily(["2024-01-03", "2024-01-04"], [3.0, 4.0])
    with pytest.raises(AssertionError, match="overlap"):
        stitch_oos_daily({"fold-1": {"daily": first}, "fold-2": {"daily": second}})


def test_annualized_sharpe_matches_known_series():
    values = [1.0, 2.0, 3.0, 4.0]
    expected = (sum(values) / len(values)) / math.sqrt(5.0 / 3.0) * math.sqrt(252.0)
    assert annualized_sharpe(values) == pytest.approx(expected)


def test_twenty_day_mbb_is_sane_and_deterministic_on_ar_series():
    values = []
    previous = 0.0
    for index in range(240):
        previous = 0.65 * previous + (1.0 if index % 7 in (0, 1, 2) else -0.4)
        values.append(previous + 0.8)
    first = mbb_ci(values, seed=20260713)
    second = mbb_ci(values, seed=20260713)
    assert first == second
    assert first[0] < sum(values) / len(values) < first[1]


def test_paired_mbb_ci_is_positive_for_constructed_dominating_series():
    candidate = [2.0 + (index % 3) * 0.01 for index in range(120)]
    baseline = [0.2 + (index % 3) * 0.01 for index in range(120)]
    low, high = paired_difference_mbb(candidate, baseline, seed=99)
    assert low > 0.0
    assert high >= low


def test_score_quintiles_are_monotonic_on_constructed_scores():
    trades = pd.DataFrame({"score": list(range(1, 11)), "net_bps": [float(value * 2) for value in range(1, 11)]})
    result = score_quintile_monotonicity(trades)
    assert result["counts"] == [2, 2, 2, 2, 2]
    assert result["monotonic"] is True
    assert result["means"] == sorted(result["means"])


def test_moving_block_bootstrap_uses_only_nonwrapping_contiguous_blocks(monkeypatch):
    starts: list[int] = []
    draws: list[list[float]] = []

    class ForcedHighStartRandom:
        def __init__(self, seed):
            pass

        def randrange(self, stop):
            starts.append(stop)
            return stop - 1

    monkeypatch.setattr(stats.random, "Random", ForcedHighStartRandom)
    stats.moving_block_bootstrap_ci(
        [0, 1, 2, 3, 4],
        block_size=3,
        samples=1,
        statistic=lambda draw: draws.append(draw) or sum(draw),
    )

    assert starts == [3, 3]
    assert draws == [[2, 3, 4, 2, 3]]


def test_deflated_sharpe_ratio_is_bounded_and_trials_one_is_positive_baseline():
    returns = [1.0, -0.5] * 10

    baseline = stats.deflated_sharpe_ratio(returns, trials=1)
    adjusted = stats.deflated_sharpe_ratio(returns, trials=100)

    assert 0.0 < baseline < 1.0
    assert baseline > 0.9
    assert adjusted < baseline

    known = [1.0, 2.0, 3.0, 4.0]
    mean = sum(known) / len(known)
    std = math.sqrt(sum((value - mean) ** 2 for value in known) / (len(known) - 1))
    sharpe = mean / std
    skew = sum(((value - mean) / std) ** 3 for value in known) / len(known)
    kurtosis = sum(((value - mean) / std) ** 4 for value in known) / len(known)
    denominator = math.sqrt(1.0 - skew * sharpe + ((kurtosis - 1.0) / 4.0) * sharpe * sharpe)
    z = sharpe * math.sqrt(len(known) - 1) / denominator
    expected = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    assert stats.deflated_sharpe_ratio(known, trials=1) == pytest.approx(expected, abs=1e-9)
