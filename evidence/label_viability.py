"""Label geometry viability audit."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


def audit_label_viability(
    rows: Iterable[dict[str, Any]] | None = None,
    *,
    target_bps: float,
    stop_bps: float,
    cost_bps: float,
    horizon_bars: int | None = None,
    horizon: int | None = None,
    side: str = "long",
    symbol_column: str = "symbol",
    timestamp_column: str = "timestamp",
    entry_column: str = "close",
    **kwargs: Any,
) -> dict[str, Any]:
    """Audit whether first-touch barrier geometry is economic before modeling.

    OHLC rows are evaluated as entries at the current bar close, then the next
    ``horizon_bars`` bars are scanned for target/stop touches. If target and
    stop are both touched in the same future bar, the conservative result is a
    stop. For backward compatibility, rows with ``forward_return_bps`` or
    ``return_bps`` are audited by the older return-threshold method.
    """
    del kwargs
    row_list = list(rows or [])
    bars = horizon_bars if horizon_bars is not None else horizon
    if _has_ohlc(row_list):
        if bars is None:
            raise ValueError("horizon_bars is required for OHLC label viability audits")
        return _audit_ohlc(
            row_list,
            target_bps=float(target_bps),
            stop_bps=float(stop_bps),
            cost_bps=float(cost_bps),
            horizon_bars=int(bars),
            side=side,
            symbol_column=symbol_column,
            timestamp_column=timestamp_column,
            entry_column=entry_column,
        )
    return _audit_forward_returns(row_list, target_bps=float(target_bps), stop_bps=float(stop_bps), cost_bps=float(cost_bps), horizon=bars)


def build_viability_table(rows: Iterable[dict[str, Any]], geometries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a JSON-serializable table for multiple target/stop/horizon tuples."""
    table: list[dict[str, Any]] = []
    row_list = list(rows)
    for geometry in geometries:
        table.append(audit_label_viability(row_list, **geometry))
    return table


def _audit_ohlc(
    rows: list[dict[str, Any]],
    *,
    target_bps: float,
    stop_bps: float,
    cost_bps: float,
    horizon_bars: int,
    side: str,
    symbol_column: str,
    timestamp_column: str,
    entry_column: str,
) -> dict[str, Any]:
    if horizon_bars <= 0:
        raise ValueError("horizon_bars must be positive")
    side_key = side.lower()
    if side_key not in {"long", "short"}:
        raise ValueError("side must be 'long' or 'short'")

    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_symbol[str(row.get(symbol_column, ""))].append(row)

    outcomes = {"target": 0, "stop": 0, "timeout": 0}
    observations = 0
    for symbol_rows in by_symbol.values():
        ordered = sorted(symbol_rows, key=lambda row: str(row.get(timestamp_column, "")))
        for index, entry_row in enumerate(ordered):
            future = ordered[index + 1 : index + 1 + horizon_bars]
            if len(future) < horizon_bars:
                continue
            entry = _float_or_none(entry_row.get(entry_column))
            if entry is None or entry <= 0:
                continue
            observations += 1
            outcome = _first_touch_outcome(future, entry, target_bps, stop_bps, side_key)
            outcomes[outcome] += 1

    natural_hit_rate = outcomes["target"] / observations if observations else 0.0
    breakeven = _breakeven_hit_rate(target_bps, stop_bps, cost_bps)
    result = {
        "target_bps": float(target_bps),
        "stop_bps": float(stop_bps),
        "cost_bps": float(cost_bps),
        "horizon_bars": horizon_bars,
        "horizon": horizon_bars,
        "side": side_key,
        "observations": observations,
        "target_hits": outcomes["target"],
        "stop_hits": outcomes["stop"],
        "timeouts": outcomes["timeout"],
        "natural_hit_rate": natural_hit_rate,
        "breakeven_hit_rate": breakeven,
        "viable": natural_hit_rate > breakeven,
        "resolution": "first_touch_stop_wins_same_bar",
    }
    return {**result, "viability_table": [_table_row(result)]}


def _first_touch_outcome(future: list[dict[str, Any]], entry: float, target_bps: float, stop_bps: float, side: str) -> str:
    if side == "long":
        target_price = entry * (1.0 + target_bps / 10000.0)
        stop_price = entry * (1.0 - stop_bps / 10000.0)
        for bar in future:
            high = _float_or_none(bar.get("high"))
            low = _float_or_none(bar.get("low"))
            if high is None or low is None:
                continue
            hit_target = high >= target_price
            hit_stop = low <= stop_price
            if hit_stop:
                return "stop"
            if hit_target:
                return "target"
    else:
        target_price = entry * (1.0 - target_bps / 10000.0)
        stop_price = entry * (1.0 + stop_bps / 10000.0)
        for bar in future:
            high = _float_or_none(bar.get("high"))
            low = _float_or_none(bar.get("low"))
            if high is None or low is None:
                continue
            hit_target = low <= target_price
            hit_stop = high >= stop_price
            if hit_stop:
                return "stop"
            if hit_target:
                return "target"
    return "timeout"


def _audit_forward_returns(rows: list[dict[str, Any]], *, target_bps: float, stop_bps: float, cost_bps: float, horizon: int | None) -> dict[str, Any]:
    wins = 0
    usable = 0
    for row in rows:
        value = row.get("forward_return_bps", row.get("return_bps"))
        if value is None:
            continue
        usable += 1
        wins += float(value) >= target_bps
    natural_hit_rate = wins / usable if usable else 0.0
    breakeven = _breakeven_hit_rate(target_bps, stop_bps, cost_bps)
    result = {
        "target_bps": target_bps,
        "stop_bps": stop_bps,
        "cost_bps": cost_bps,
        "horizon": horizon,
        "horizon_bars": horizon,
        "side": "long",
        "observations": usable,
        "target_hits": wins,
        "stop_hits": None,
        "timeouts": None,
        "natural_hit_rate": natural_hit_rate,
        "breakeven_hit_rate": breakeven,
        "viable": natural_hit_rate > breakeven,
        "resolution": "forward_return_threshold_compatibility",
    }
    return {**result, "viability_table": [_table_row(result)]}


def _breakeven_hit_rate(target_bps: float, stop_bps: float, cost_bps: float) -> float:
    if stop_bps + cost_bps <= 0 or target_bps <= cost_bps:
        return 1.0
    return (stop_bps + cost_bps) / (target_bps + stop_bps)


def _table_row(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "target_bps": result["target_bps"],
        "stop_bps": result["stop_bps"],
        "cost_bps": result["cost_bps"],
        "horizon_bars": result["horizon_bars"],
        "side": result["side"],
        "observations": result["observations"],
        "natural_hit_rate": result["natural_hit_rate"],
        "breakeven_hit_rate": result["breakeven_hit_rate"],
        "viable": result["viable"],
    }


def _has_ohlc(rows: list[dict[str, Any]]) -> bool:
    return any({"open", "high", "low", "close"}.issubset(row.keys()) for row in rows)


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
