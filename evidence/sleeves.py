"""Deterministic sleeve phase-runner report helpers."""
from __future__ import annotations

import json
import math
from collections import Counter
from datetime import date, datetime
from typing import Any, Iterable, Mapping


class SleevePromotionThresholds:
    ci_low_min_bps = 0.0
    min_trades = 300
    min_trading_days = 100
    max_single_symbol_share = 0.20
    min_distinct_symbols = 20
    min_positive_fold_share = 0.70
    worst_fold_floor_bps = 0.0
    min_dsr = 0.0


DEFAULT_PROMOTION_THRESHOLDS = SleevePromotionThresholds()


class FuturesSleevePromotionThresholds:
    ci_low_min_bps = 0.0
    min_trades = 100
    min_trading_days = 60
    min_positive_fold_share = 0.60
    worst_fold_floor_bps = 0.0
    min_dsr = 0.0


FUTURES_PROMOTION_THRESHOLDS = FuturesSleevePromotionThresholds()


def canonical_report(
    *,
    sleeve: str,
    phase: int,
    prerequisites: Mapping[str, Any],
    gates: Mapping[str, Any],
    candidate: Mapping[str, Any] | None = None,
    promoted: bool = False,
    reason: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the common JSON-safe report shape used by sleeve research runners."""
    status = "promoted" if promoted else "quarantined"
    payload: dict[str, Any] = {
        "sleeve": sleeve,
        "phase": int(phase),
        "real_feed_validated": bool(prerequisites.get("real_feed_validated", False)),
        "candidate": dict(candidate or {}),
        "prerequisites": dict(prerequisites),
        "gates": dict(gates),
        "status": status,
    }
    if reason:
        payload["reason"] = reason
    if extra:
        payload.update(dict(extra))
    return json_safe(payload)


def promotion_gates_from_metrics(
    *,
    trades: int,
    trading_days: int,
    distinct_symbols: int,
    max_symbol_trade_share: float,
    positive_fold_share: float = 0.0,
    worst_fold_bps: float = 0.0,
    ci_low_bps: float = 0.0,
    beats_baselines: bool = False,
    dsr: float = 0.0,
    sealed_test_passed: bool = False,
    fixture_mode: bool = False,
) -> dict[str, bool]:
    thresholds = DEFAULT_PROMOTION_THRESHOLDS
    gates = {
        "ci_low_positive_full_cost": float(ci_low_bps) > thresholds.ci_low_min_bps,
        "validation_trade_count": int(trades) >= thresholds.min_trades,
        "validation_trading_days": int(trading_days) >= thresholds.min_trading_days,
        "symbol_concentration": float(max_symbol_trade_share) <= thresholds.max_single_symbol_share,
        "min_distinct_symbols": int(distinct_symbols) >= thresholds.min_distinct_symbols,
        "folds_positive_share": float(positive_fold_share) >= thresholds.min_positive_fold_share,
        "worst_fold_floor": float(worst_fold_bps) >= thresholds.worst_fold_floor_bps,
        "beats_rung0_baselines_net_sharpe": bool(beats_baselines),
        "deflated_sharpe_positive": float(dsr) > thresholds.min_dsr,
        "sealed_test_once_passed": bool(sealed_test_passed),
        "fixture_mode_not_promotable": not fixture_mode,
    }
    return gates


def futures_promotion_gates_from_metrics(
    *,
    trades: int,
    trading_days: int,
    positive_fold_share: float = 0.0,
    worst_fold_bps: float = 0.0,
    ci_low_bps: float = 0.0,
    dsr: float = 0.0,
    sealed_test_passed: bool = False,
    fixture_mode: bool = False,
) -> dict[str, bool]:
    thresholds = FUTURES_PROMOTION_THRESHOLDS
    return {
        "ci_low_positive_full_cost": float(ci_low_bps) > thresholds.ci_low_min_bps,
        "validation_trade_count": int(trades) >= thresholds.min_trades,
        "validation_trading_days": int(trading_days) >= thresholds.min_trading_days,
        "folds_positive_share": float(positive_fold_share) >= thresholds.min_positive_fold_share,
        "worst_fold_floor": float(worst_fold_bps) >= thresholds.worst_fold_floor_bps,
        "deflated_sharpe_positive": float(dsr) > thresholds.min_dsr,
        "sealed_test_once_passed": bool(sealed_test_passed),
        "fixture_mode_not_promotable": not fixture_mode,
    }


def gate_failure_reason(gates: Mapping[str, bool], default: str = "promotion_gates_failed") -> str:
    failed = [name for name, passed in gates.items() if not passed]
    return failed[0] if failed else default


def concentration(symbols: Iterable[Any]) -> dict[str, Any]:
    counts = Counter(str(symbol) for symbol in symbols if symbol is not None)
    total = sum(counts.values())
    return {
        "max_symbol_trade_share": max(counts.values()) / total if total else 1.0,
        "distinct_symbols": len(counts),
        "symbol_trade_counts": dict(sorted(counts.items())),
    }


def trading_day_count(rows: Iterable[Mapping[str, Any]]) -> int:
    return len({_date_key(row) for row in rows if _date_key(row)})


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return 0.0
        return value
    return value


def _date_key(row: Mapping[str, Any]) -> str | None:
    value = row.get("date", row.get("timestamp"))
    if value is None:
        return None
    text = str(value)
    return text[:10] if len(text) >= 10 else text
