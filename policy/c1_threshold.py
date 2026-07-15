"""Registered C1 activity-threshold fitting.

Thresholds are fitted against executed trades, rather than raw eligible rows.
That distinction is important: ``evidence.c1_replay.replay`` applies the
position-exclusivity rule, the daily three-trade cap, and the daily halt.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

import pandas as pd

from evidence.c1_folds import inner_validation_split
from evidence.c1_replay import ReplayConfig, _prepare_replay, _prepare_rows, _threshold_trade_counts


DEFAULT_ACTIVITY_RATE = Decimal("0.70")
DEFAULT_SLEEVE_CAPITAL = 1_000_000.0


@dataclass(frozen=True)
class ThresholdFitResult:
    """Frozen threshold and the diagnostics needed to audit its fit."""

    threshold: float
    target: int
    achieved_count: int
    candidates_evaluated: tuple[float, ...]
    achieved_counts: tuple[int, ...]

    @property
    def diagnostics(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "achieved_count": self.achieved_count,
            "candidates_evaluated": list(self.candidates_evaluated),
            "achieved_counts": list(self.achieved_counts),
        }

    def __iter__(self):
        """Allow ``threshold, diagnostics = fit_threshold(...)``."""
        yield self.threshold
        yield self.diagnostics


def round_half_up(value: float | Decimal) -> int:
    """Round a decimal value away from the half-to-even default at ties."""
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def fit_threshold(
    validation_rows: pd.DataFrame,
    *,
    score_column: str = "score",
    activity_rate: float | Decimal = DEFAULT_ACTIVITY_RATE,
    sleeve_capital: float = DEFAULT_SLEEVE_CAPITAL,
    contract_calendar: pd.DataFrame | str | None = None,
    replay_config: ReplayConfig | None = None,
) -> ThresholdFitResult:
    """Fit one absolute threshold on a purged inner-validation slice.

    Every candidate is evaluated by the registered replay engine.  Candidate
    scores are the unique finite values in the slice; a score equal to the
    threshold is eligible, matching replay's frozen ``>=`` rule.
    """
    if not isinstance(validation_rows, pd.DataFrame) or validation_rows.empty:
        raise ValueError("validation_rows must be a non-empty DataFrame")
    if score_column not in validation_rows:
        raise ValueError(f"validation_rows needs {score_column!r}")
    if Decimal(str(activity_rate)) < 0:
        raise ValueError("activity_rate must be non-negative")

    scored = validation_rows.copy()
    if score_column != "score":
        scored["score"] = scored[score_column]
    scores = pd.to_numeric(scored["score"], errors="coerce")
    candidates = sorted(float(value) for value in scores.dropna().unique())
    if not candidates:
        raise ValueError("validation_rows must contain at least one finite score")

    sessions = _session_count(scored)
    target = round_half_up(Decimal(str(activity_rate)) * sessions)
    if sleeve_capital <= 0:
        raise ValueError("sleeve_capital must be positive")
    config = replay_config or ReplayConfig()
    if config.horizon_bars < 1 or config.max_trades_per_day < 1:
        raise ValueError("horizon_bars and max_trades_per_day must be positive")
    prepared = _prepare_replay(_prepare_rows(scored, contract_calendar), config)
    counts = list(_threshold_trade_counts(prepared, candidates, config))

    # The second key selects fewer executed trades.  The third key makes the
    # intended direction explicit if two candidate scores have the same count.
    winner = min(
        range(len(candidates)),
        key=lambda index: (
            abs(counts[index] - target),
            counts[index],
            -candidates[index],
        ),
    )
    return ThresholdFitResult(
        threshold=candidates[winner],
        target=target,
        achieved_count=counts[winner],
        candidates_evaluated=tuple(candidates),
        achieved_counts=tuple(counts),
    )


def fit_fold_threshold(
    purged_train_rows: pd.DataFrame,
    *,
    validation_rows: pd.DataFrame | None = None,
    **kwargs: Any,
) -> ThresholdFitResult:
    """Split a purged fold's training rows and fit on its final 20%.

    Callers that already performed ``inner_validation_split`` may pass the
    resulting validation frame explicitly.  Otherwise this function performs
    the registered chronological split itself; that split purges the earlier
    inner-train side at the validation boundary and leaves validation intact.
    """
    if validation_rows is None:
        _inner_train, validation_rows = inner_validation_split(purged_train_rows)
    return fit_threshold(validation_rows, **kwargs)


def _session_count(rows: pd.DataFrame) -> int:
    if "trade_date" in rows:
        sessions = pd.to_datetime(rows["trade_date"], errors="raise").dt.normalize()
    elif "datetime" in rows:
        sessions = pd.to_datetime(rows["datetime"], errors="raise").dt.normalize()
    else:
        raise ValueError("validation_rows needs trade_date or datetime")
    count = int(sessions.nunique())
    if count < 1:
        raise ValueError("validation_rows must contain at least one session")
    return count
