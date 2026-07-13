"""Registered Sleeve F C1 replay state machine."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Any

import pandas as pd

from policy.c1_sizing import (
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
    days = sorted(rows["trade_date"].unique())
    day_bars = {
        day: group.sort_values("datetime", kind="stable").reset_index(drop=True)
        for day, group in rows.groupby("trade_date", sort=True)
    }
    candidates = _eligible_candidates(rows, threshold)
    accepted: list[dict[str, Any]] = []
    day_trade_records: dict[date, list[dict[str, Any]]] = {day: [] for day in days}
    day_counts: dict[date, int] = {day: 0 for day in days}

    for candidate in candidates.itertuples(index=False):
        day = candidate.trade_date
        if day_counts[day] >= cfg.max_trades_per_day:
            continue
        bars = day_bars[day]
        decision_index = _index_for_timestamp(bars, candidate.datetime)
        entry_index = decision_index + 1
        if entry_index >= len(bars):
            continue

        simulation = _simulate_trade(
            bars,
            decision_index=decision_index,
            entry_index=entry_index,
            score=float(candidate.score),
            requested_multiplier=float(candidate.requested_multiplier),
            base_lots=int(candidate.base_lots),
            front_expiry=candidate.front_expiry,
            contract_id=candidate.contract_id,
            config=cfg,
        )
        if any(simulation["_entry_index"] <= other["_exit_index"] for other in day_trade_records[day]):
            continue

        realized_records = [
            record
            for record in day_trade_records[day]
            if record["_exit_index"] < entry_index
        ]
        if is_daily_halted(realized_records, day):
            continue
        accepted.append(simulation)
        day_trade_records[day].append(simulation)
        day_counts[day] += 1

    trades = _trade_frame(accepted)
    daily = _daily_frame(days, day_trade_records, sleeve_capital, cfg)
    return ReplayResult(
        trades=trades,
        daily=daily,
        metadata={
            "threshold": float(threshold),
            "sleeve_capital": float(sleeve_capital),
            "target_pct": cfg.target_pct,
            "stop_pct": cfg.stop_pct,
            "horizon_bars": cfg.horizon_bars,
            "max_trades_per_day": cfg.max_trades_per_day,
            "stress_slippage_bps": list(cfg.stress_slippage_bps),
            "eligibility": "09:45 <= decision < 14:55; 11:00 <= decision < 12:00 excluded; compression excluded",
            "calendar": "validated front-contract calendar",
        },
    )


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


def _simulate_trade(
    bars: pd.DataFrame,
    *,
    decision_index: int,
    entry_index: int,
    score: float,
    requested_multiplier: float,
    base_lots: int,
    front_expiry: date,
    contract_id: str,
    config: ReplayConfig,
) -> dict[str, Any]:
    trade_date = bars["trade_date"].iat[entry_index]
    entry_bar = bars.iloc[entry_index]
    entry_price = float(entry_bar["f_open"])
    target_price = entry_price * (1.0 + config.target_pct)
    stop_price = entry_price * (1.0 - config.stop_pct)
    walk_end = min(len(bars), entry_index + config.horizon_bars)
    walk = bars.iloc[entry_index:walk_end]
    exit_index = int(walk.index[-1])
    exit_price = float(walk["f_close"].iloc[-1])
    exit_reason = "EXPIRY" if trade_date == front_expiry else "TIMEOUT"
    for index, bar in walk.iterrows():
        if float(bar["f_open"]) <= stop_price or float(bar["f_low"]) <= stop_price:
            exit_index, exit_price, exit_reason = int(index), stop_price, "STOP"
            break
        if float(bar["f_open"]) >= target_price or float(bar["f_high"]) >= target_price:
            exit_index, exit_price, exit_reason = int(index), target_price, "TARGET"
            break

    size = size_position(
        trade_date,
        entry_price,
        base_lots=base_lots,
        requested_multiplier=requested_multiplier,
    )
    gross = (exit_price - entry_price) * size.quantity
    costs = cost_rupees(trade_date, entry_price, exit_price, lots=size.lots)
    realized = floor_trade_pnl(gross - costs["total"], size.entry_notional)
    record: dict[str, Any] = {
        "trade_date": trade_date,
        "decision_datetime": bars["datetime"].iat[decision_index],
        "entry_datetime": bars["datetime"].iat[entry_index],
        "exit_datetime": bars["datetime"].iat[exit_index],
        "contract_id": str(contract_id),
        "front_expiry": front_expiry,
        "score": score,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "target_price": target_price,
        "stop_price": stop_price,
        "exit_reason": exit_reason,
        "lot_size": size.lot_size,
        "lots": size.lots,
        "quantity": size.quantity,
        "requested_multiplier": requested_multiplier,
        "multiplier": size.multiplier,
        "entry_notional": size.entry_notional,
        "gross_pnl": gross,
        "cost_rupees": costs["total"],
        "realized_pnl": realized,
        "realized_r": trade_pnl_r(realized, size.entry_notional),
        "_entry_index": entry_index,
        "_exit_index": exit_index,
        "_size": size,
    }
    for slippage in config.stress_slippage_bps:
        stress_cost = cost_rupees(
            trade_date,
            entry_price,
            exit_price,
            lots=size.lots,
            slippage_bps=slippage,
        )["total"]
        record[_stress_column("cost", slippage)] = stress_cost
        record[_stress_column("pnl", slippage)] = floor_trade_pnl(
            gross - stress_cost, size.entry_notional
        )
    return record


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
