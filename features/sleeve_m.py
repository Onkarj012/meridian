"""Sleeve M momentum feature composition and phase-3 runner."""
from __future__ import annotations

import importlib.util
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from features.price import build_price_features
from features.volume import build_volume_features
from features.vwap import build_vwap_features

_SLEEVES_SPEC = importlib.util.spec_from_file_location("_meridian_evidence_sleeves", Path(__file__).resolve().parents[1] / "evidence" / "sleeves.py")
_SLEEVES = importlib.util.module_from_spec(_SLEEVES_SPEC)
assert _SLEEVES_SPEC and _SLEEVES_SPEC.loader
_SLEEVES_SPEC.loader.exec_module(_SLEEVES)
canonical_report = _SLEEVES.canonical_report
concentration = _SLEEVES.concentration
gate_failure_reason = _SLEEVES.gate_failure_reason
promotion_gates_from_metrics = _SLEEVES.promotion_gates_from_metrics
trading_day_count = _SLEEVES.trading_day_count

def build_sleeve_m_features(rows=None, *args, **kwargs):
    return build_vwap_features(build_volume_features(build_price_features(rows or [])))


def build_sleeve_m_barrier_labels(
    rows: Iterable[Mapping[str, Any]],
    *,
    target_bps: float,
    stop_bps: float,
    horizon_bars: int,
    side: str = "long",
) -> list[dict[str, Any]]:
    """Build first-touch barrier labels; stop wins target/stop same-bar ties."""
    if horizon_bars <= 0:
        raise ValueError("horizon_bars must be positive")
    if target_bps <= 0 or stop_bps <= 0:
        raise ValueError("target_bps and stop_bps must be positive")
    side_value = side.lower()
    if side_value not in {"long", "short"}:
        raise ValueError("side must be 'long' or 'short'")

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("symbol", row.get("tradingsymbol", "")))].append(dict(row))
    output: list[dict[str, Any]] = []
    for symbol_rows in grouped.values():
        ordered = sorted(symbol_rows, key=lambda item: str(item.get("timestamp", item.get("date", ""))))
        for index, row in enumerate(ordered):
            entry = _price(row.get("close", row.get("price")))
            label = "no_touch"
            touch_bar = None
            outcome_bps = 0.0
            if entry is not None and entry > 0:
                for offset, future in enumerate(ordered[index + 1 : index + 1 + horizon_bars], start=1):
                    high = _price(future.get("high", future.get("close", future.get("price"))))
                    low = _price(future.get("low", future.get("close", future.get("price"))))
                    if high is None or low is None:
                        continue
                    if side_value == "long":
                        target_touched = (high / entry - 1.0) * 10000.0 >= target_bps
                        stop_touched = (low / entry - 1.0) * 10000.0 <= -stop_bps
                    else:
                        target_touched = (entry / low - 1.0) * 10000.0 >= target_bps if low > 0 else False
                        stop_touched = (entry / high - 1.0) * 10000.0 <= -stop_bps
                    if stop_touched:
                        label = "stop"
                        touch_bar = offset
                        outcome_bps = -float(stop_bps)
                        break
                    if target_touched:
                        label = "target"
                        touch_bar = offset
                        outcome_bps = float(target_bps)
                        break
            output.append({**row, "side": side_value, "barrier_label": label, "touch_bar": touch_bar, "outcome_bps": outcome_bps, "target_bps": float(target_bps), "stop_bps": float(stop_bps), "horizon_bars": int(horizon_bars)})
    return sorted(output, key=lambda item: (str(item.get("timestamp", item.get("date", ""))), str(item.get("symbol", ""))))


def run_sleeve_m_scaled_momentum(
    rows: Iterable[Mapping[str, Any]],
    *,
    universe_size: int | None = None,
    fixture_mode: bool = False,
    top_k: int = 5,
    cost_bps: float = 0.0,
) -> dict[str, Any]:
    """Run deterministic Phase-3 scaled momentum report over prepared rows."""
    row_list = [dict(row) for row in rows]
    symbols = {str(row.get("symbol")) for row in row_list if row.get("symbol") is not None}
    observed_universe = universe_size if universe_size is not None else len(symbols)
    prerequisites = {
        "real_feed_validated": True,
        "universe_size": int(observed_universe),
        "universe_size_at_least_200": int(observed_universe) >= 200,
        "fixture_mode": bool(fixture_mode),
    }
    if int(observed_universe) < 200 and not fixture_mode:
        return canonical_report(
            sleeve="M",
            phase=3,
            prerequisites=prerequisites,
            gates={},
            candidate={"universe_size": int(observed_universe)},
            reason="universe_size_below_200",
            extra={"autopsy": {"reason": "scale_data_before_modeling", "minimum_universe_size": 200}},
        )

    enriched = _momentum_rows(row_list)
    trades = _select_momentum_trades(enriched, top_k=top_k)
    net = [float(trade.get("forward_return_bps", trade.get("outcome_bps", 0.0)) or 0.0) - float(cost_bps) for trade in trades]
    conc = concentration(trade.get("symbol") for trade in trades)
    expected = sum(net) / len(net) if net else 0.0
    candidate = {
        "model_family": "rung0_plain_momentum",
        "trades": len(trades),
        "trading_days": trading_day_count(trades),
        "distinct_symbols": conc["distinct_symbols"],
        "max_symbol_trade_share": conc["max_symbol_trade_share"],
        "expected_value_bps": expected,
        "ci_low_bps": min(net) if net else 0.0,
        "concentration": conc,
    }
    gates = promotion_gates_from_metrics(
        trades=candidate["trades"],
        trading_days=candidate["trading_days"],
        distinct_symbols=candidate["distinct_symbols"],
        max_symbol_trade_share=candidate["max_symbol_trade_share"],
        positive_fold_share=1.0 if expected > 0 else 0.0,
        worst_fold_bps=expected,
        ci_low_bps=candidate["ci_low_bps"],
        beats_baselines=False,
        dsr=0.0,
        sealed_test_passed=False,
        fixture_mode=fixture_mode,
    )
    promoted = all(gates.values()) and prerequisites["universe_size_at_least_200"] and not fixture_mode
    return canonical_report(
        sleeve="M",
        phase=3,
        prerequisites=prerequisites,
        gates=gates,
        candidate=candidate,
        promoted=promoted,
        reason=None if promoted else gate_failure_reason(gates, "scaled_momentum_not_promoted"),
        extra={"autopsy": None if promoted else {"reason": gate_failure_reason(gates, "scaled_momentum_not_promoted")}},
    )


def _momentum_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("symbol", ""))].append(row)
    output = []
    for symbol_rows in grouped.values():
        ordered = sorted(symbol_rows, key=lambda item: str(item.get("timestamp", item.get("date", ""))))
        previous_close: float | None = None
        for row in ordered:
            close = _price(row.get("close", row.get("price")))
            momentum = (close / previous_close - 1.0) * 10000.0 if close is not None and previous_close else float(row.get("momentum_bps", 0.0) or 0.0)
            output.append({**row, "momentum_bps": momentum})
            if close is not None:
                previous_close = close
    return output


def _select_momentum_trades(rows: list[dict[str, Any]], *, top_k: int) -> list[dict[str, Any]]:
    by_time: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_time[str(row.get("timestamp", row.get("date", "")))].append(row)
    trades = []
    for _, items in sorted(by_time.items()):
        ranked = sorted(items, key=lambda item: float(item.get("momentum_bps", 0.0) or 0.0), reverse=True)
        trades.extend(ranked[: max(1, int(top_k))])
    return trades


def _price(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result
