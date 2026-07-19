"""Registered Sleeve F C1 lot sizing and restart-proof R-unit risk controls."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from policy.era_costs import ERA_TABLE


RISK_UNIT_BPS = 30.0
TRADE_FLOOR_R = -1.0
DAILY_HALT_R = -5.0


@dataclass(frozen=True)
class PositionSize:
    """Integer-lot position selected for one entry."""

    trade_date: date
    lot_size: int
    lots: int
    quantity: int
    multiplier: float
    entry_notional: float
    risk_unit_rupees: float


@dataclass(frozen=True)
class DailyRiskState:
    """A daily risk state reconstructed entirely from realized trade records."""

    trade_date: date
    realized_r: float
    halted: bool


def lot_size_for(trade_date: date | datetime | str) -> int:
    """Return the C1 reporting-era NIFTY lot size for ``trade_date``."""
    day = _as_date(trade_date)
    matching = [era for era in ERA_TABLE if date.fromisoformat(str(era["effective_date"])) <= day]
    if not matching:
        raise ValueError("C1 lot schedule begins on 2020-01-01")
    return int(matching[-1]["lot_size"])


def risk_unit(entry_notional: float) -> float:
    """Return ``R_t``: 30 bps of positive entry notional."""
    notional = float(entry_notional)
    if notional <= 0:
        raise ValueError("entry_notional must be positive")
    return notional * RISK_UNIT_BPS / 10_000.0


def size_position(
    trade_date: date | datetime | str,
    entry_price: float,
    *,
    base_lots: int = 1,
    requested_multiplier: float = 1.0,
) -> PositionSize:
    """Size an entry in date-specific integer lots.

    Rounding rule: a requested 1.5x size is used only when
    ``base_lots * 1.5`` is already a whole number; otherwise it falls back to
    exactly 1.0x.  C1 never rounds a fractional lot up or down.
    """
    price = float(entry_price)
    if price <= 0:
        raise ValueError("entry_price must be positive")
    if isinstance(base_lots, bool) or int(base_lots) != base_lots or base_lots < 1:
        raise ValueError("base_lots must be a positive integer")
    if requested_multiplier not in (1.0, 1.5):
        raise ValueError("requested_multiplier must be 1.0 or 1.5")

    lots = int(base_lots)
    multiplier = 1.0
    requested_lots = lots * float(requested_multiplier)
    if requested_multiplier == 1.5 and requested_lots.is_integer():
        lots = int(requested_lots)
        multiplier = 1.5

    day = _as_date(trade_date)
    lot_size = lot_size_for(day)
    quantity = lot_size * lots
    entry_notional = price * quantity
    return PositionSize(
        trade_date=day,
        lot_size=lot_size,
        lots=lots,
        quantity=quantity,
        multiplier=multiplier,
        entry_notional=entry_notional,
        risk_unit_rupees=risk_unit(entry_notional),
    )


def floor_trade_pnl(realized_pnl: float, entry_notional: float) -> float:
    """Apply the C1 per-trade floor of negative one R to realized PnL."""
    return max(float(realized_pnl), TRADE_FLOOR_R * risk_unit(entry_notional))


def trade_pnl_r(realized_pnl: float, entry_notional: float) -> float:
    """Return floor-adjusted realized PnL in risk units."""
    unit = risk_unit(entry_notional)
    return floor_trade_pnl(realized_pnl, entry_notional) / unit


def daily_risk_state(
    trade_log: Iterable[Mapping[str, Any]],
    trade_date: date | datetime | str,
) -> DailyRiskState:
    """Reconstruct a day's halt state from its realized trade log.

    Each matching record needs ``trade_date``, ``entry_notional``, and
    ``realized_pnl``.  Replaying this function after a process restart gives
    the same state, so the daily halt is not dependent on in-memory state.
    """
    day = _as_date(trade_date)
    realized_r = 0.0
    for record in _records(trade_log):
        if _as_date(record["trade_date"]) != day:
            continue
        realized_r += trade_pnl_r(record["realized_pnl"], record["entry_notional"])
    return DailyRiskState(trade_date=day, realized_r=realized_r, halted=realized_r <= DAILY_HALT_R)


def is_daily_halted(
    trade_log: Iterable[Mapping[str, Any]],
    trade_date: date | datetime | str,
) -> bool:
    """Return whether realized, floor-adjusted losses have reached negative five R."""
    return daily_risk_state(trade_log, trade_date).halted


def _records(trade_log: Iterable[Mapping[str, Any]]) -> Iterable[Mapping[str, Any]]:
    if hasattr(trade_log, "to_dict"):
        return trade_log.to_dict("records")
    return trade_log


def _as_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)
