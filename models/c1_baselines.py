"""Registered C1 activity-matched causal baselines.

Baseline scores are deliberately only a ranking over eligible decision bars.
The final trade set is always produced by :func:`evidence.c1_replay.replay`,
including its exclusivity, three-trades-per-day cap, costs, sizing, and halt
rules.  Matching therefore searches the baseline candidate-set size rather
than bypassing replay with a post-hoc trade-count adjustment.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from statistics import median
from typing import Any, Mapping

import numpy as np
import pandas as pd

from evidence.c1_replay import ReplayConfig, ReplayResult, prepare_static_replay, replay_scores


RANDOM_ENTRY_REPLICATES = 1_000
BASELINE_THRESHOLD = 0.5
PROTOCOL_VERSION = "sleeve-f-c1-v1"


@dataclass(frozen=True)
class BaselineResult:
    """One replayed baseline plus its deterministic matching audit trail."""

    name: str
    replay_result: ReplayResult
    target_trade_count: int
    candidate_set_size: int
    executed_trade_count: int
    seed: int | None = None
    null_distribution: tuple[dict[str, Any], ...] = ()

    @property
    def trades(self) -> pd.DataFrame:
        return self.replay_result.trades

    @property
    def daily(self) -> pd.DataFrame:
        return self.replay_result.daily

    @property
    def metadata(self) -> dict[str, Any]:
        return self.replay_result.metadata


def random_entry_seed(
    protocol_version: str,
    candidate: str,
    fold: str,
    replicate: int,
) -> int:
    """Derive the registered uint32 seed from the four identifying fields."""
    if int(replicate) < 0:
        raise ValueError("replicate must be non-negative")
    payload = f"{protocol_version}|{candidate}|{fold}|{int(replicate)}".encode("utf-8")
    return int.from_bytes(sha256(payload).digest()[:4], "big", signed=False)


# Alias makes the seed rule easy to discover without creating a second
# convention.  The candidate module is not present in this working tree.
seed_for_random_entry = random_entry_seed


def activity_matched_baselines(
    scored_rows: pd.DataFrame,
    candidate_trades: pd.DataFrame | ReplayResult | int,
    *,
    protocol_version: str = PROTOCOL_VERSION,
    candidate: str = "candidate",
    fold: str = "fold",
    sleeve_capital: float,
    contract_calendar: pd.DataFrame | str | None = None,
    config: ReplayConfig | None = None,
    random_replicates: int = RANDOM_ENTRY_REPLICATES,
) -> dict[str, BaselineResult]:
    """Build all four registered baselines for one candidate fold.

    ``candidate_trades`` is the fold's *executed* trade artifact (or its
    integer count).  For every baseline, candidate-set sizes are tried from
    zero upward.  An exact executed-count match wins; if replay mechanics make
    it impossible, the closest executed count wins, with ties going to the
    lower executed count and then the smaller candidate set.  This is the
    frozen deterministic tie-down rule.

    Random-entry retains every replicate's complete daily series in
    ``BaselineResult.null_distribution``.  Its representative replay is the
    lower-median total-return replicate, while ``median_total_return_bps`` in
    its metadata is the comparison statistic.
    """
    if int(random_replicates) < 1:
        raise ValueError("random_replicates must be positive")
    rows = _normalise_rows(scored_rows)
    target = _executed_count(candidate_trades)

    # The calendar join and barrier-exit resolution depend only on the base
    # frame (OHLC bars + calendar), never on which bars are scored. Building
    # this once and reusing it across every candidate-set size and every
    # random-baseline replicate is what makes ~1,000 replicates tractable;
    # each of those previously re-ran the full replay() prepare step.
    resolved_calendar = contract_calendar if contract_calendar is not None else _calendar_from_rows(rows)
    static = prepare_static_replay(rows, contract_calendar=resolved_calendar, config=config)
    eligible = static.rows.loc[static.eligible].copy()

    baselines: dict[str, BaselineResult] = {
        "time_of_day": _match_ranked(
            static,
            _time_of_day_order(eligible, candidate_trades),
            target,
            name="time_of_day",
            sleeve_capital=sleeve_capital,
        ),
        "volatility": _match_ranked(
            static,
            _volatility_order(eligible),
            target,
            name="volatility",
            sleeve_capital=sleeve_capital,
        ),
        "unconditional_long": _match_ranked(
            static,
            _earliest_order(eligible),
            target,
            name="unconditional_long",
            sleeve_capital=sleeve_capital,
        ),
    }

    random_results: list[BaselineResult] = []
    for replicate in range(int(random_replicates)):
        seed = random_entry_seed(protocol_version, candidate, fold, replicate)
        random_results.append(
            _match_ranked(
                static,
                _random_order(eligible, seed),
                target,
                name="random_entry",
                sleeve_capital=sleeve_capital,
                seed=seed,
            )
        )

    totals = [sum(float(value) for value in result.daily["policy_return_bps"]) for result in random_results]
    lower_median_total = sorted(totals)[(len(totals) - 1) // 2]
    representative_index = next(index for index, total in enumerate(totals) if total == lower_median_total)
    distribution = tuple(_daily_summary(result, replicate) for replicate, result in enumerate(random_results))
    representative = random_results[representative_index]
    baselines["random_entry"] = BaselineResult(
        name=representative.name,
        replay_result=representative.replay_result,
        target_trade_count=representative.target_trade_count,
        candidate_set_size=representative.candidate_set_size,
        executed_trade_count=representative.executed_trade_count,
        seed=representative.seed,
        null_distribution=distribution,
    )
    baselines["random_entry"].replay_result.metadata.update(
        {
            "replicates": int(random_replicates),
            "median_total_return_bps": float(median(totals)),
            "representative_replicate": representative_index,
        }
    )
    return baselines


def build_activity_matched_baselines(*args: Any, **kwargs: Any) -> dict[str, BaselineResult]:
    """Discoverable alias for :func:`activity_matched_baselines`."""
    return activity_matched_baselines(*args, **kwargs)


def _normalise_rows(rows: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(rows, pd.DataFrame) or rows.empty:
        raise ValueError("scored_rows must be a non-empty DataFrame")
    required = {"datetime", "f_open", "f_high", "f_low", "f_close"}
    missing = sorted(required - set(rows.columns))
    if missing:
        raise ValueError(f"scored_rows missing columns: {', '.join(missing)}")
    result = rows.copy().reset_index(drop=True)
    result["datetime"] = pd.to_datetime(result["datetime"], errors="raise")
    if "score" not in result and "long_score" not in result:
        result["score"] = 0.0
    return result


def _executed_count(candidate_trades: pd.DataFrame | ReplayResult | int) -> int:
    if isinstance(candidate_trades, ReplayResult):
        return len(candidate_trades.trades)
    if isinstance(candidate_trades, pd.DataFrame):
        return len(candidate_trades)
    count = int(candidate_trades)
    if count < 0:
        raise ValueError("candidate trade count must be non-negative")
    return count


def _calendar_from_rows(rows: pd.DataFrame) -> pd.DataFrame | None:
    if "front_expiry" not in rows:
        return None
    calendar = rows[["trade_date", "front_expiry"]].copy() if "trade_date" in rows else rows.assign(
        trade_date=rows["datetime"].dt.date
    )[["trade_date", "front_expiry"]]
    calendar["front_instrument_id"] = calendar["front_expiry"].astype(str)
    return calendar.drop_duplicates("trade_date")


def _time_of_day_order(eligible: pd.DataFrame, candidate_trades: Any) -> list[int]:
    """Rank candidate minutes by fold frequency, then minute and timestamp.

    The most-traded candidate decision minutes come first; frequency ties are
    resolved toward the earlier minute.  If replay needs more bars to reach
    the target count, the same deterministic ordering expands to the next
    minutes rather than changing the matching rule.
    """
    eligible = eligible.copy()
    if isinstance(candidate_trades, ReplayResult):
        trades = candidate_trades.trades
    elif isinstance(candidate_trades, pd.DataFrame):
        trades = candidate_trades
    else:
        trades = pd.DataFrame()
    counts: dict[int, int] = {}
    if not trades.empty and "decision_datetime" in trades:
        minutes = pd.to_datetime(trades["decision_datetime"], errors="raise").dt.hour * 60 + pd.to_datetime(
            trades["decision_datetime"], errors="raise"
        ).dt.minute
        counts = minutes.value_counts().astype(int).to_dict()
    eligible["minute_of_day"] = eligible["datetime"].dt.hour * 60 + eligible["datetime"].dt.minute
    eligible["minute_frequency"] = eligible["minute_of_day"].map(counts).fillna(0)
    return eligible.sort_values(
        ["minute_frequency", "minute_of_day", "datetime", "_input_order"],
        ascending=[False, True, True, True],
        kind="stable",
    ).index.tolist()


def _volatility_order(eligible: pd.DataFrame) -> list[int]:
    eligible = eligible.copy()
    column = next((name for name in ("realized_vol_30m", "trailing_realized_vol_30m", "volatility") if name in eligible), None)
    if column is None:
        raise ValueError("volatility baseline requires realized_vol_30m")
    eligible["_volatility"] = pd.to_numeric(eligible[column], errors="coerce").fillna(float("-inf"))
    return eligible.sort_values(
        ["_volatility", "datetime", "_input_order"],
        ascending=[False, True, True],
        kind="stable",
    ).index.tolist()


def _earliest_order(eligible: pd.DataFrame) -> list[int]:
    return eligible.sort_values(["datetime", "_input_order"], kind="stable").index.tolist()


def _random_order(eligible: pd.DataFrame, seed: int) -> list[int]:
    order = eligible.index.tolist()
    import random

    random.Random(int(seed) & 0xFFFFFFFF).shuffle(order)
    return order


def _match_ranked(
    static: Any,
    order: list[int],
    target: int,
    *,
    name: str,
    sleeve_capital: float,
    seed: int | None = None,
) -> BaselineResult:
    n = len(static.rows)
    candidates = static.rows.loc[order] if order else static.rows.iloc[0:0]
    candidate_positions = candidates.index.to_numpy()
    best: tuple[tuple[int, int, int], ReplayResult, int] | None = None
    for candidate_set_size in range(0, len(candidates) + 1):
        scores = np.zeros(n, dtype=float)
        if candidate_set_size:
            selected_positions = candidate_positions[:candidate_set_size]
            scores[selected_positions] = np.arange(candidate_set_size, 0, -1, dtype=float)
        result = replay_scores(static, scores, BASELINE_THRESHOLD, sleeve_capital=sleeve_capital)
        executed = len(result.trades)
        key = (abs(executed - target), executed, candidate_set_size)
        if best is None or key < best[0]:
            best = (key, result, candidate_set_size)
        if executed == target:
            break
    assert best is not None
    key, result, candidate_set_size = best
    result.metadata.update(
        {
            "baseline": name,
            "target_executed_trade_count": int(target),
            "candidate_set_size": int(candidate_set_size),
            "matching": "exact first; otherwise closest count, lower executed count, smaller set",
            "matched_exactly": bool(key[0] == 0),
        }
    )
    return BaselineResult(
        name=name,
        replay_result=result,
        target_trade_count=int(target),
        candidate_set_size=int(candidate_set_size),
        executed_trade_count=len(result.trades),
        seed=seed,
    )


def _daily_summary(result: BaselineResult, replicate: int) -> dict[str, Any]:
    daily = result.daily.reset_index()
    return {
        "replicate": int(replicate),
        "seed": result.seed,
        "candidate_set_size": result.candidate_set_size,
        "executed_trade_count": result.executed_trade_count,
        "total_return_bps": float(daily["policy_return_bps"].sum()),
        "daily": [
            {"trade_date": str(row.trade_date), "policy_return_bps": float(row.policy_return_bps)}
            for row in daily.itertuples(index=False)
        ],
    }
