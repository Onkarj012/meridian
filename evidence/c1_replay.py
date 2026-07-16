"""Registered Sleeve F C1 replay state machine."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np

from policy.c1_sizing import (
    DAILY_HALT_R,
    floor_trade_pnl,
    is_daily_halted,
    size_position,
    trade_pnl_r,
)
from policy.era_costs import STRESS_SLIPPAGE_BPS, cost_rupees


TARGET_PCT = 0.0040
STOP_PCT = 0.0030
HORIZON_BARS = 60
MAX_TRADES_PER_DAY = 3
DEFAULT_CALENDAR_PATH = Path(__file__).resolve().parents[1] / "runs/sleeve-f-data-contract/contract_calendar.csv"
TRADE_COLUMNS = (
    "trade_date",
    "decision_datetime",
    "entry_datetime",
    "exit_datetime",
    "contract_id",
    "front_expiry",
    "score",
    "entry_price",
    "exit_price",
    "target_price",
    "stop_price",
    "exit_reason",
    "lot_size",
    "lots",
    "quantity",
    "requested_multiplier",
    "multiplier",
    "entry_notional",
    "gross_pnl",
    "cost_rupees",
    "realized_pnl",
    "realized_r",
)


@dataclass(frozen=True)
class ReplayConfig:
    """Frozen mechanics for one registered replay."""

    target_pct: float = TARGET_PCT
    stop_pct: float = STOP_PCT
    horizon_bars: int = HORIZON_BARS
    max_trades_per_day: int = MAX_TRADES_PER_DAY
    base_lots: int = 1
    stress_slippage_bps: tuple[float, ...] = STRESS_SLIPPAGE_BPS


@dataclass(frozen=True)
class ReplayResult:
    """Per-trade and per-session policy-return artifacts."""

    trades: pd.DataFrame
    daily: pd.DataFrame
    metadata: dict[str, Any]

    def __iter__(self):
        """Allow the conventional ``trades, daily`` unpacking form."""
        yield self.trades
        yield self.daily


@dataclass(frozen=True)
class _StaticReplay:
    """Numpy views of one prepared frame that do not depend on scores.

    Calendar-join, day segmentation, and barrier-exit resolution only read
    OHLC bars and the contract calendar, so they are identical for every
    score assignment tried against the same base frame (e.g. every
    candidate-set size and every random-baseline replicate). Building this
    once and reusing it is what makes the activity-matched baseline search
    (which replays the same frame under many score assignments) tractable.
    """

    rows: pd.DataFrame
    config: ReplayConfig
    days: tuple[date, ...]
    day_codes: np.ndarray
    day_starts: np.ndarray
    day_ends: np.ndarray
    duplicate_timestamps: np.ndarray
    eligible: np.ndarray
    candidate_indices: np.ndarray
    datetime_ns: np.ndarray
    datetimes: np.ndarray
    trade_dates: np.ndarray
    front_expiries: np.ndarray
    contract_ids: np.ndarray
    requested_multipliers: np.ndarray
    base_lots: np.ndarray
    f_open: np.ndarray
    f_high: np.ndarray
    f_low: np.ndarray
    f_close: np.ndarray
    exit_indices: np.ndarray
    exit_prices: np.ndarray
    exit_reasons: np.ndarray


@dataclass(frozen=True)
class _PreparedReplay:
    """Numpy views of one prepared frame shared by replay evaluations."""

    rows: pd.DataFrame
    days: tuple[date, ...]
    day_codes: np.ndarray
    day_starts: np.ndarray
    day_ends: np.ndarray
    duplicate_timestamps: np.ndarray
    eligible: np.ndarray
    candidate_order: np.ndarray
    datetimes: np.ndarray
    trade_dates: np.ndarray
    front_expiries: np.ndarray
    contract_ids: np.ndarray
    scores: np.ndarray
    requested_multipliers: np.ndarray
    base_lots: np.ndarray
    f_open: np.ndarray
    f_high: np.ndarray
    f_low: np.ndarray
    f_close: np.ndarray
    exit_indices: np.ndarray
    exit_prices: np.ndarray
    exit_reasons: np.ndarray


def replay(
    scored_rows: pd.DataFrame,
    threshold: float,
    *,
    sleeve_capital: float,
    contract_calendar: pd.DataFrame | Path | str | None = None,
    config: ReplayConfig | None = None,
) -> ReplayResult:
    """Replay scored bars into registered trades and daily sleeve returns."""
    cfg = config or ReplayConfig()
    if sleeve_capital <= 0:
        raise ValueError("sleeve_capital must be positive")
    if cfg.horizon_bars < 1 or cfg.max_trades_per_day < 1:
        raise ValueError("horizon_bars and max_trades_per_day must be positive")

    rows = _prepare_rows(scored_rows, contract_calendar)
    static = _prepare_static(rows, cfg)
    prepared = _apply_scores(static, rows["score"].to_numpy(dtype=float))
    trades, daily = _replay_prepared(prepared, threshold, sleeve_capital, cfg)
    return ReplayResult(trades=trades, daily=daily, metadata=_replay_metadata(threshold, sleeve_capital, cfg))


def _replay_metadata(threshold: float, sleeve_capital: float, cfg: ReplayConfig) -> dict[str, Any]:
    return {
        "threshold": float(threshold),
        "sleeve_capital": float(sleeve_capital),
        "target_pct": cfg.target_pct,
        "stop_pct": cfg.stop_pct,
        "horizon_bars": cfg.horizon_bars,
        "max_trades_per_day": cfg.max_trades_per_day,
        "stress_slippage_bps": list(cfg.stress_slippage_bps),
        "eligibility": "09:45 <= decision < 14:55; 11:00 <= decision < 12:00 excluded; compression excluded",
        "calendar": "validated front-contract calendar",
    }


def prepare_static_replay(
    scored_rows: pd.DataFrame,
    *,
    contract_calendar: pd.DataFrame | Path | str | None = None,
    config: ReplayConfig | None = None,
) -> _StaticReplay:
    """Precompute the score-independent replay structure for one base frame.

    Callers that replay the same base rows under many different score
    assignments (activity-matched baseline search, which tries a range of
    candidate-set sizes and, for the random-entry baseline, ~1,000
    replicates) should build this once and pass it to :func:`replay_scores`
    instead of calling :func:`replay` per assignment — each :func:`replay`
    call redundantly redoes the calendar join and barrier-exit resolution,
    which do not depend on scores.
    """
    cfg = config or ReplayConfig()
    rows = _prepare_rows(scored_rows, contract_calendar)
    return _prepare_static(rows, cfg)


def replay_scores(
    static: _StaticReplay,
    scores: np.ndarray,
    threshold: float,
    *,
    sleeve_capital: float,
) -> ReplayResult:
    """Replay ``static`` under a given score assignment.

    Equivalent to calling :func:`replay` with a frame carrying ``scores`` in
    its score column, but skips the calendar join and exit precomputation
    already captured in ``static``.
    """
    cfg = static.config
    if sleeve_capital <= 0:
        raise ValueError("sleeve_capital must be positive")
    prepared = _apply_scores(static, np.asarray(scores, dtype=float))
    trades, daily = _replay_prepared(prepared, threshold, sleeve_capital, cfg)
    return ReplayResult(trades=trades, daily=daily, metadata=_replay_metadata(threshold, sleeve_capital, cfg))


def _replay_prepared(
    prepared: _PreparedReplay,
    threshold: float,
    sleeve_capital: float,
    config: ReplayConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    candidates = prepared.candidate_order
    days = list(prepared.days)
    accepted: list[dict[str, Any]] = []
    day_trade_records: dict[date, list[dict[str, Any]]] = {day: [] for day in days}
    day_counts: dict[date, int] = {day: 0 for day in days}

    for candidate_index in candidates:
        candidate_index = int(candidate_index)
        score = float(prepared.scores[candidate_index])
        if score < float(threshold):
            continue
        day_code = int(prepared.day_codes[candidate_index])
        day = days[day_code]
        if day_counts[day] >= config.max_trades_per_day:
            continue
        if prepared.duplicate_timestamps[candidate_index]:
            raise ValueError("each decision timestamp must identify exactly one bar per session")
        entry_index = candidate_index + 1
        if entry_index >= prepared.day_ends[day_code]:
            continue

        if any(entry_index <= other["_exit_index"] for other in day_trade_records[day]):
            continue

        realized_records = [
            record
            for record in day_trade_records[day]
            if record["_exit_index"] < entry_index
        ]
        if is_daily_halted(realized_records, day):
            continue
        simulation = _prepared_trade_record(prepared, candidate_index, config)
        accepted.append(simulation)
        day_trade_records[day].append(simulation)
        day_counts[day] += 1

    trades = _trade_frame(accepted)
    daily = _daily_frame(days, day_trade_records, sleeve_capital, config)
    return trades, daily


def eligible_rows(
    scored_rows: pd.DataFrame,
    *,
    contract_calendar: pd.DataFrame | Path | str | None = None,
) -> pd.DataFrame:
    """Return rows passing the registered decision-bar eligibility rules."""
    rows = _prepare_rows(scored_rows, contract_calendar)
    return rows.loc[_eligibility_mask(rows)].copy()


def _prepare_rows(
    rows: pd.DataFrame,
    contract_calendar: pd.DataFrame | Path | str | None,
) -> pd.DataFrame:
    if not isinstance(rows, pd.DataFrame) or rows.empty:
        raise ValueError("scored_rows must be a non-empty DataFrame")
    score_column = "score" if "score" in rows else "long_score" if "long_score" in rows else None
    if score_column is None:
        raise ValueError("scored_rows needs a score or long_score column")
    required = {"datetime", "f_open", "f_high", "f_low", "f_close"}
    missing = sorted(required - set(rows.columns))
    if missing:
        raise ValueError(f"scored_rows missing columns: {', '.join(missing)}")

    result = rows.copy()
    result["datetime"] = pd.to_datetime(result["datetime"], errors="raise")
    result["trade_date"] = result["datetime"].map(_as_date)
    result["score"] = pd.to_numeric(result[score_column], errors="coerce")
    result["_input_order"] = range(len(result))
    multiplier_values = result["size_mult"] if "size_mult" in result else result.get("requested_multiplier")
    result["requested_multiplier"] = (
        pd.to_numeric(multiplier_values, errors="coerce").fillna(1.0)
        if multiplier_values is not None
        else 1.0
    )
    base_lots_values = result.get("base_lots")
    result["base_lots"] = (
        pd.to_numeric(base_lots_values, errors="raise").astype(int)
        if base_lots_values is not None
        else 1
    )
    calendar = _load_calendar(contract_calendar)
    result = result.drop(columns=[column for column in ("front_expiry", "front_instrument_id") if column in result])
    result = result.merge(calendar, how="left", on="trade_date", validate="many_to_one")
    if result["front_expiry"].isna().any():
        dates = sorted(result.loc[result["front_expiry"].isna(), "trade_date"].unique())
        raise ValueError(f"contract calendar missing trade dates: {dates}")
    result["front_expiry"] = pd.to_datetime(result["front_expiry"], errors="raise").dt.date
    if (result["front_expiry"] < result["trade_date"]).any():
        raise ValueError("front contract expiry precedes trade date")
    result["contract_id"] = result["front_instrument_id"].fillna(result["front_expiry"].astype(str))
    return result.sort_values(["trade_date", "datetime", "_input_order"], kind="stable").reset_index(drop=True)


def _prepare_replay(rows: pd.DataFrame, config: ReplayConfig) -> _PreparedReplay:
    """Compatibility wrapper: build the full prepared frame in one call.

    ``policy/c1_threshold.py`` calls this directly for its own single-frame
    threshold-fitting sweep, which does not repeat over many score
    assignments and so doesn't need the static/scores split.
    """
    static = _prepare_static(rows, config)
    return _apply_scores(static, rows["score"].to_numpy(dtype=float))


def _prepare_static(rows: pd.DataFrame, config: ReplayConfig) -> _StaticReplay:
    """Build the per-frame arrays that do not depend on scores."""
    trade_dates = rows["trade_date"].to_numpy(dtype=object)
    starts = np.flatnonzero(np.r_[True, trade_dates[1:] != trade_dates[:-1]])
    ends = np.r_[starts[1:], len(rows)]
    day_codes = np.repeat(np.arange(len(starts), dtype=np.int64), ends - starts)
    days = tuple(trade_dates[starts].tolist())

    eligible = _eligibility_mask(rows).to_numpy(dtype=bool)
    candidate_indices = np.flatnonzero(eligible)
    datetime_ns = rows["datetime"].astype("int64").to_numpy()

    f_open = rows["f_open"].to_numpy(dtype=float)
    f_high = rows["f_high"].to_numpy(dtype=float)
    f_low = rows["f_low"].to_numpy(dtype=float)
    f_close = rows["f_close"].to_numpy(dtype=float)
    exit_indices, exit_prices, exit_reasons = _precompute_exits(
        trade_dates,
        rows["front_expiry"].to_numpy(dtype=object),
        day_codes,
        ends,
        f_open,
        f_high,
        f_low,
        f_close,
        config,
    )
    return _StaticReplay(
        rows=rows,
        config=config,
        days=days,
        day_codes=day_codes,
        day_starts=starts,
        day_ends=ends,
        duplicate_timestamps=rows.duplicated(["trade_date", "datetime"], keep=False).to_numpy(dtype=bool),
        eligible=eligible,
        candidate_indices=candidate_indices,
        datetime_ns=datetime_ns,
        datetimes=rows["datetime"].to_numpy(),
        trade_dates=trade_dates,
        front_expiries=rows["front_expiry"].to_numpy(dtype=object),
        contract_ids=rows["contract_id"].to_numpy(dtype=object),
        requested_multipliers=rows["requested_multiplier"].to_numpy(dtype=float),
        base_lots=rows["base_lots"].to_numpy(dtype=np.int64),
        f_open=f_open,
        f_high=f_high,
        f_low=f_low,
        f_close=f_close,
        exit_indices=exit_indices,
        exit_prices=exit_prices,
        exit_reasons=exit_reasons,
    )


def _apply_scores(static: _StaticReplay, scores: np.ndarray) -> _PreparedReplay:
    """Attach one score assignment to a precomputed static frame."""
    candidate_indices = static.candidate_indices
    if len(candidate_indices):
        order = np.lexsort(
            (
                static.rows["_input_order"].to_numpy(dtype=np.int64)[candidate_indices],
                -scores[candidate_indices],
                static.datetime_ns[candidate_indices],
            )
        )
        candidate_order = candidate_indices[order]
    else:
        candidate_order = candidate_indices
    return _PreparedReplay(
        rows=static.rows,
        days=static.days,
        day_codes=static.day_codes,
        day_starts=static.day_starts,
        day_ends=static.day_ends,
        duplicate_timestamps=static.duplicate_timestamps,
        eligible=static.eligible,
        candidate_order=candidate_order,
        datetimes=static.datetimes,
        trade_dates=static.trade_dates,
        front_expiries=static.front_expiries,
        contract_ids=static.contract_ids,
        scores=scores,
        requested_multipliers=static.requested_multipliers,
        base_lots=static.base_lots,
        f_open=static.f_open,
        f_high=static.f_high,
        f_low=static.f_low,
        f_close=static.f_close,
        exit_indices=static.exit_indices,
        exit_prices=static.exit_prices,
        exit_reasons=static.exit_reasons,
    )


def _precompute_exits(
    trade_dates: np.ndarray,
    front_expiries: np.ndarray,
    day_codes: np.ndarray,
    day_ends: np.ndarray,
    f_open: np.ndarray,
    f_high: np.ndarray,
    f_low: np.ndarray,
    f_close: np.ndarray,
    config: ReplayConfig,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Resolve the entry-bar barrier state for every possible decision row."""
    row_indices = np.arange(len(trade_dates), dtype=np.int64)
    entry_indices = row_indices + 1
    valid_entry = entry_indices < day_ends[day_codes]
    safe_entry = np.minimum(entry_indices, len(trade_dates) - 1)
    target_prices = f_open[safe_entry] * (1.0 + config.target_pct)
    stop_prices = f_open[safe_entry] * (1.0 - config.stop_pct)
    walk_ends = np.minimum(day_ends[day_codes], entry_indices + config.horizon_bars)
    default_exits = np.clip(walk_ends - 1, 0, len(trade_dates) - 1)
    exit_indices = default_exits.copy()
    exit_prices = f_close[default_exits].copy()
    exit_reasons = np.asarray(
        ["EXPIRY" if day == expiry else "TIMEOUT" for day, expiry in zip(trade_dates, front_expiries)],
        dtype=object,
    )
    unresolved = valid_entry.copy()

    for offset in range(config.horizon_bars):
        bar_indices = entry_indices + offset
        safe_bar = np.minimum(bar_indices, len(trade_dates) - 1)
        active = unresolved & (bar_indices < day_ends[day_codes])
        stop_hit = active & ((f_open[safe_bar] <= stop_prices) | (f_low[safe_bar] <= stop_prices))
        target_hit = active & ~stop_hit & (
            (f_open[safe_bar] >= target_prices) | (f_high[safe_bar] >= target_prices)
        )
        exit_indices[stop_hit] = bar_indices[stop_hit]
        exit_prices[stop_hit] = stop_prices[stop_hit]
        exit_reasons[stop_hit] = "STOP"
        exit_indices[target_hit] = bar_indices[target_hit]
        exit_prices[target_hit] = target_prices[target_hit]
        exit_reasons[target_hit] = "TARGET"
        unresolved[stop_hit | target_hit] = False

    return exit_indices, exit_prices, exit_reasons


def _load_calendar(source: pd.DataFrame | Path | str | None) -> pd.DataFrame:
    if source is None:
        source = DEFAULT_CALENDAR_PATH
    calendar = pd.read_csv(source) if not isinstance(source, pd.DataFrame) else source.copy()
    required = {"trade_date", "front_expiry"}
    missing = sorted(required - set(calendar.columns))
    if missing:
        raise ValueError(f"contract_calendar missing columns: {', '.join(missing)}")
    calendar["trade_date"] = pd.to_datetime(calendar["trade_date"], errors="raise").dt.date
    calendar["front_expiry"] = pd.to_datetime(calendar["front_expiry"], errors="raise").dt.date
    if calendar["trade_date"].duplicated().any():
        raise ValueError("contract_calendar has duplicate trade dates")
    if "front_instrument_id" not in calendar:
        calendar["front_instrument_id"] = calendar["front_expiry"].astype(str)
    return calendar[["trade_date", "front_expiry", "front_instrument_id"]]


def _eligibility_mask(rows: pd.DataFrame) -> pd.Series:
    times = rows["datetime"].dt.time
    mask = (times >= dtime(9, 45)) & (times < dtime(14, 55))
    mask &= ~((times >= dtime(11, 0)) & (times < dtime(12, 0)))
    mask &= rows["score"].notna()
    if "eligible" in rows:
        mask &= rows["eligible"].fillna(False).astype(bool)
    if "eligibility_pass" in rows:
        mask &= rows["eligibility_pass"].fillna(False).astype(bool)
    if "time_eligible" in rows:
        mask &= rows["time_eligible"].fillna(False).astype(bool)
    if "compression_excluded" in rows:
        mask &= ~rows["compression_excluded"].fillna(False).astype(bool)
    for column in (
        "is_session_eligible",
        "is_time_eligible",
        "is_warmup_eligible",
        "class_a_eligible",
        "regime_eligible",
        "label_window_contiguous",
        "is_decision_eligible",
    ):
        if column in rows:
            mask &= rows[column].fillna(False).astype(bool)
    if "label_excluded" in rows:
        mask &= ~rows["label_excluded"].fillna(False).astype(bool)
    for column in ("regime", "causal_regime", "regime_label"):
        if column in rows:
            mask &= rows[column].fillna("").astype(str).str.lower().ne("compression")
    return mask


def _eligible_candidates(rows: pd.DataFrame, threshold: float) -> pd.DataFrame:
    candidates = rows.loc[_eligibility_mask(rows) & (rows["score"] >= float(threshold))].copy()
    return candidates.sort_values(
        ["trade_date", "datetime", "score", "_input_order"],
        ascending=[True, True, False, True],
        kind="stable",
    )


def _trade_values(
    prepared: _PreparedReplay,
    candidate_index: int,
    config: ReplayConfig,
) -> tuple[dict[str, Any], Any]:
    entry_index = candidate_index + 1
    trade_date = prepared.trade_dates[entry_index]
    entry_price = float(prepared.f_open[entry_index])
    size = size_position(
        trade_date,
        entry_price,
        base_lots=int(prepared.base_lots[candidate_index]),
        requested_multiplier=float(prepared.requested_multipliers[candidate_index]),
    )
    exit_index = int(prepared.exit_indices[candidate_index])
    exit_price = float(prepared.exit_prices[candidate_index])
    gross = (exit_price - entry_price) * size.quantity
    costs = cost_rupees(trade_date, entry_price, exit_price, lots=size.lots)
    realized = floor_trade_pnl(gross - costs["total"], size.entry_notional)
    return {
        "trade_date": trade_date,
        "decision_datetime": prepared.datetimes[candidate_index],
        "entry_datetime": prepared.datetimes[entry_index],
        "exit_datetime": prepared.datetimes[exit_index],
        "contract_id": str(prepared.contract_ids[candidate_index]),
        "front_expiry": prepared.front_expiries[candidate_index],
        "score": float(prepared.scores[candidate_index]),
        "entry_price": entry_price,
        "exit_price": exit_price,
        "target_price": entry_price * (1.0 + config.target_pct),
        "stop_price": entry_price * (1.0 - config.stop_pct),
        "exit_reason": prepared.exit_reasons[candidate_index],
        "size": size,
        "gross": gross,
        "costs": costs,
        "realized": realized,
    }, size


def _prepared_trade_record(
    prepared: _PreparedReplay,
    candidate_index: int,
    config: ReplayConfig,
) -> dict[str, Any]:
    values, size = _trade_values(prepared, candidate_index, config)
    gross = values["gross"]
    costs = values["costs"]
    realized = values["realized"]
    record: dict[str, Any] = {
        "trade_date": values["trade_date"],
        "decision_datetime": values["decision_datetime"],
        "entry_datetime": values["entry_datetime"],
        "exit_datetime": values["exit_datetime"],
        "contract_id": values["contract_id"],
        "front_expiry": values["front_expiry"],
        "score": values["score"],
        "entry_price": values["entry_price"],
        "exit_price": values["exit_price"],
        "target_price": values["target_price"],
        "stop_price": values["stop_price"],
        "exit_reason": values["exit_reason"],
        "lot_size": size.lot_size,
        "lots": size.lots,
        "quantity": size.quantity,
        "requested_multiplier": float(prepared.requested_multipliers[candidate_index]),
        "multiplier": size.multiplier,
        "entry_notional": size.entry_notional,
        "gross_pnl": gross,
        "cost_rupees": costs["total"],
        "realized_pnl": realized,
        "realized_r": trade_pnl_r(realized, size.entry_notional),
        "_entry_index": candidate_index + 1,
        "_exit_index": int(prepared.exit_indices[candidate_index]),
        "_size": size,
    }
    for slippage in config.stress_slippage_bps:
        stress_cost = cost_rupees(
            values["trade_date"],
            values["entry_price"],
            values["exit_price"],
            lots=size.lots,
            slippage_bps=slippage,
        )["total"]
        record[_stress_column("cost", slippage)] = stress_cost
        record[_stress_column("pnl", slippage)] = floor_trade_pnl(
            gross - stress_cost, size.entry_notional
        )
    return record


def _prepared_trade_r(
    prepared: _PreparedReplay,
    candidate_index: int,
    config: ReplayConfig,
) -> float:
    values, size = _trade_values(prepared, candidate_index, config)
    return trade_pnl_r(values["realized"], size.entry_notional)


def _threshold_trade_counts(
    prepared: _PreparedReplay,
    thresholds: list[float],
    config: ReplayConfig,
) -> tuple[int, ...]:
    """Evaluate every threshold with one chronological numpy state sweep."""
    threshold_values = np.asarray(thresholds, dtype=float)
    threshold_count = len(threshold_values)
    day_count = len(prepared.days)
    counts = np.zeros(threshold_count, dtype=np.int64)
    last_exit = np.full((threshold_count, day_count), -1, dtype=np.int64)
    day_counts = np.zeros((threshold_count, day_count), dtype=np.int64)
    realized_r = np.zeros((threshold_count, day_count), dtype=float)
    halted = np.zeros((threshold_count, day_count), dtype=bool)

    for candidate_index_value in prepared.candidate_order:
        candidate_index = int(candidate_index_value)
        score = float(prepared.scores[candidate_index])
        active_end = int(np.searchsorted(threshold_values, score, side="right"))
        if active_end == 0:
            continue
        day_code = int(prepared.day_codes[candidate_index])
        entry_index = candidate_index + 1
        if np.all(day_counts[:active_end, day_code] >= config.max_trades_per_day):
            continue
        if prepared.duplicate_timestamps[candidate_index]:
            raise ValueError("each decision timestamp must identify exactly one bar per session")
        if entry_index >= prepared.day_ends[day_code]:
            continue

        eligible = (
            (day_counts[:active_end, day_code] < config.max_trades_per_day)
            & (last_exit[:active_end, day_code] < entry_index)
            & ~halted[:active_end, day_code]
        )
        accepted = np.flatnonzero(eligible)
        if len(accepted) == 0:
            continue

        candidate_r = _prepared_trade_r(prepared, candidate_index, config)
        accepted_global = accepted
        day_counts[accepted_global, day_code] += 1
        last_exit[accepted_global, day_code] = prepared.exit_indices[candidate_index]
        realized_r[accepted_global, day_code] += candidate_r
        halted[accepted_global, day_code] = realized_r[accepted_global, day_code] <= DAILY_HALT_R
        counts[accepted_global] += 1

    return tuple(int(value) for value in counts)


def _intervals_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left["trade_date"] != right["trade_date"]:
        return False
    return max(left["_entry_index"], right["_entry_index"]) <= min(left["_exit_index"], right["_exit_index"])


def _trade_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(columns=TRADE_COLUMNS)
    frame = pd.DataFrame([{column: record[column] for column in TRADE_COLUMNS} for record in records])
    return frame.sort_values(["trade_date", "entry_datetime"], kind="stable").reset_index(drop=True)


def _daily_frame(
    days: list[date],
    day_records: dict[date, list[dict[str, Any]]],
    sleeve_capital: float,
    config: ReplayConfig,
) -> pd.DataFrame:
    rows: list[dict[str, float | date | int]] = []
    for day in days:
        records = day_records[day]
        values: dict[str, float | date | int] = {
            "trade_date": day,
            "trade_count": len(records),
            "policy_return_bps": sum(record["realized_pnl"] for record in records) / sleeve_capital * 10_000.0,
        }
        for slippage in config.stress_slippage_bps:
            values[_stress_column("return_bps", slippage)] = (
                sum(record[_stress_column("pnl", slippage)] for record in records)
                / sleeve_capital
                * 10_000.0
            )
        rows.append(values)
    return pd.DataFrame(rows).set_index("trade_date")


def _stress_column(prefix: str, slippage: float) -> str:
    return f"stress_{str(slippage).replace('.', '_')}_{prefix}"


def _index_for_timestamp(bars: pd.DataFrame, timestamp: pd.Timestamp) -> int:
    matches = bars.index[bars["datetime"] == timestamp]
    if len(matches) != 1:
        raise ValueError("each decision timestamp must identify exactly one bar per session")
    return int(matches[0])


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return pd.Timestamp(value).date()
