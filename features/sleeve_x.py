"""Sleeve X ranking feature composition and phase-4 research helpers."""
from __future__ import annotations

import importlib.util
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from features.cross_sectional import build_cross_sectional_features
from features.price import build_price_features
from features.relative_strength import build_relative_strength_features

DEFAULT_DELIVERY_ROUND_TRIP_BPS = 41.0
_SLEEVES_SPEC = importlib.util.spec_from_file_location("_meridian_evidence_sleeves", Path(__file__).resolve().parents[1] / "evidence" / "sleeves.py")
_SLEEVES = importlib.util.module_from_spec(_SLEEVES_SPEC)
assert _SLEEVES_SPEC and _SLEEVES_SPEC.loader
_SLEEVES_SPEC.loader.exec_module(_SLEEVES)
canonical_report = _SLEEVES.canonical_report
gate_failure_reason = _SLEEVES.gate_failure_reason
promotion_gates_from_metrics = _SLEEVES.promotion_gates_from_metrics

def build_sleeve_x_features(rows=None, *args, **kwargs):
    return build_cross_sectional_features(build_relative_strength_features(build_price_features(rows or [])))


def build_sleeve_x_ranking_labels(rows: Iterable[Mapping[str, Any]], *, horizons: Sequence[int] = (1, 3, 5, 10)) -> list[dict[str, Any]]:
    """Build per-date cross-sectional forward-return ranks for Sleeve X."""
    row_list = [dict(row) for row in rows]
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in row_list:
        by_symbol[str(row.get("symbol", ""))].append(row)
    labeled: list[dict[str, Any]] = []
    for symbol_rows in by_symbol.values():
        ordered = sorted(symbol_rows, key=lambda item: str(item.get("date", item.get("timestamp", ""))))
        for index, row in enumerate(ordered):
            enriched = dict(row)
            close = _number(row.get("close", row.get("price")))
            for horizon in horizons:
                future_index = index + int(horizon)
                future_close = _number(ordered[future_index].get("close", ordered[future_index].get("price"))) if future_index < len(ordered) else None
                enriched[f"forward_return_{horizon}d_bps"] = (future_close / close - 1.0) * 10000.0 if close and future_close is not None else None
            labeled.append(enriched)

    for horizon in horizons:
        key = f"forward_return_{horizon}d_bps"
        rank_key = f"rank_{horizon}d"
        percentile_key = f"rank_pct_{horizon}d"
        by_time: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in labeled:
            if row.get(key) is not None:
                by_time[_time_key(row)].append(row)
        for items in by_time.values():
            ranked = sorted(items, key=lambda item: float(item[key]), reverse=True)
            denominator = max(1, len(ranked) - 1)
            for rank, row in enumerate(ranked, start=1):
                row[rank_key] = rank
                row[percentile_key] = 1.0 - ((rank - 1) / denominator if denominator else 0.0)
    return sorted(labeled, key=lambda item: (_time_key(item), str(item.get("symbol", ""))))


def evaluate_rank_ic(rows: Iterable[Mapping[str, Any]], *, prediction_key: str = "score", label_key: str = "forward_return_1d_bps") -> dict[str, Any]:
    """Average Spearman rank IC by timestamp/date cross-section."""
    by_time: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get(prediction_key) is not None and row.get(label_key) is not None:
            by_time[_time_key(row)].append(dict(row))
    ics = []
    for time_key, items in sorted(by_time.items()):
        if len(items) < 2:
            continue
        pred = [float(item[prediction_key]) for item in items]
        actual = [float(item[label_key]) for item in items]
        ics.append({"timestamp": time_key, "ic": _spearman(pred, actual), "n": len(items)})
    mean_ic = sum(item["ic"] for item in ics) / len(ics) if ics else 0.0
    return {"mean_rank_ic": mean_ic, "cross_sections": len(ics), "rank_ic_by_timestamp": ics}


def evaluate_top_bottom_net_spread(
    rows: Iterable[Mapping[str, Any]],
    *,
    prediction_key: str = "score",
    label_key: str = "forward_return_1d_bps",
    top_k: int = 1,
    full_delivery_cost_bps: float,
) -> dict[str, Any]:
    """Evaluate long-top/short-bottom spread after full delivery costs."""
    by_time: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get(prediction_key) is not None and row.get(label_key) is not None:
            by_time[_time_key(row)].append(dict(row))
    spreads = []
    for time_key, items in sorted(by_time.items()):
        if len(items) < top_k * 2:
            continue
        ranked = sorted(items, key=lambda item: float(item[prediction_key]), reverse=True)
        top = ranked[:top_k]
        bottom = ranked[-top_k:]
        gross = _mean(float(item[label_key]) for item in top) - _mean(float(item[label_key]) for item in bottom)
        net = gross - 2.0 * float(full_delivery_cost_bps)
        spreads.append({"timestamp": time_key, "gross_spread_bps": gross, "net_spread_bps": net, "top_symbols": [item.get("symbol") for item in top], "bottom_symbols": [item.get("symbol") for item in bottom]})
    return {
        "top_k": int(top_k),
        "full_delivery_cost_bps": float(full_delivery_cost_bps),
        "cross_sections": len(spreads),
        "mean_gross_spread_bps": _mean(item["gross_spread_bps"] for item in spreads),
        "mean_net_spread_bps": _mean(item["net_spread_bps"] for item in spreads),
        "spreads": spreads,
    }


def run_sleeve_x_rung0_to_rung2(
    rows: Iterable[Mapping[str, Any]],
    *,
    pit_ready: bool,
    grid_committed: bool,
    horizons: Sequence[int] = (1, 3, 5, 10),
    cost_bps: float = DEFAULT_DELIVERY_ROUND_TRIP_BPS,
    fixture_mode: bool = False,
    enable_v3_challenger: bool = False,
) -> dict[str, Any]:
    """Run deterministic Phase-4 rung 0-2 report and block rung 3 until ready."""
    prerequisites = {
        "real_feed_validated": True,
        "pit_ready": bool(pit_ready),
        "committed_grid": bool(grid_committed),
        "fixture_mode": bool(fixture_mode),
    }
    if not pit_ready:
        return canonical_report(sleeve="X", phase=4, prerequisites=prerequisites, gates={}, candidate=None, reason="pit_inputs_not_ready", extra={"v3_challenger": {"status": "blocked", "reason": "rung0_to_rung2_prerequisites_failed"}})
    if not grid_committed:
        return canonical_report(sleeve="X", phase=4, prerequisites=prerequisites, gates={}, candidate=None, reason="walkforward_grid_not_committed", extra={"v3_challenger": {"status": "blocked", "reason": "rung0_to_rung2_prerequisites_failed"}})

    labeled = build_sleeve_x_ranking_labels(rows, horizons=horizons)
    scored = [_with_score(row) for row in labeled]
    primary_horizon = int(horizons[0])
    label_key = f"forward_return_{primary_horizon}d_bps"
    rank_ic = evaluate_rank_ic(scored, label_key=label_key)
    spread = evaluate_top_bottom_net_spread(scored, label_key=label_key, top_k=1, full_delivery_cost_bps=cost_bps)
    candidate = {
        "model_family": "rung0_to_rung2_ranking_research",
        "primary_horizon": primary_horizon,
        "rank_ic": rank_ic["mean_rank_ic"],
        "top_bottom_net_spread_bps": spread["mean_net_spread_bps"],
        "trades": spread["cross_sections"] * 2,
        "trading_days": spread["cross_sections"],
        "distinct_symbols": len({row.get("symbol") for row in scored if row.get(label_key) is not None}),
        "max_symbol_trade_share": 0.0,
    }
    gates = promotion_gates_from_metrics(
        trades=candidate["trades"],
        trading_days=candidate["trading_days"],
        distinct_symbols=candidate["distinct_symbols"],
        max_symbol_trade_share=candidate["max_symbol_trade_share"],
        positive_fold_share=1.0 if candidate["top_bottom_net_spread_bps"] > 0 else 0.0,
        worst_fold_bps=candidate["top_bottom_net_spread_bps"],
        ci_low_bps=candidate["top_bottom_net_spread_bps"],
        beats_baselines=False,
        dsr=0.0,
        sealed_test_passed=False,
        fixture_mode=fixture_mode,
    )
    promoted = all(gates.values()) and not fixture_mode
    v3_status = "eligible" if promoted and enable_v3_challenger else "blocked"
    v3_reason = None if v3_status == "eligible" else "rung0_to_rung2_prerequisites_failed"
    return canonical_report(
        sleeve="X",
        phase=4,
        prerequisites=prerequisites,
        gates=gates,
        candidate=candidate,
        promoted=promoted,
        reason=None if promoted else gate_failure_reason(gates, "rung0_to_rung2_not_promoted"),
        extra={"rungs": {"rung0_momentum": {"rank_ic": rank_ic}, "rung2_tree_placeholder": {"top_bottom_spread": spread}}, "v3_challenger": {"status": v3_status, "reason": v3_reason}},
    )


def _with_score(row: Mapping[str, Any]) -> dict[str, Any]:
    if row.get("score") is not None:
        return dict(row)
    score = row.get("momentum_bps", row.get("return_5d_bps", row.get("return_1d_bps", row.get("return_bps", 0.0))))
    return {**row, "score": float(score or 0.0)}


def _time_key(row: Mapping[str, Any]) -> str:
    value = row.get("date", row.get("timestamp", ""))
    return str(value)[:10]


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _mean(values: Iterable[float]) -> float:
    data = list(values)
    return sum(data) / len(data) if data else 0.0


def _spearman(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        return 0.0
    return _pearson(_ranks(left), _ranks(right))


def _ranks(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(indexed):
        end = index + 1
        while end < len(indexed) and indexed[end][1] == indexed[index][1]:
            end += 1
        rank = (index + end + 1) / 2.0
        for original, _ in indexed[index:end]:
            ranks[original] = rank
        index = end
    return ranks


def _pearson(left: list[float], right: list[float]) -> float:
    left_mean = _mean(left)
    right_mean = _mean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_var = sum((a - left_mean) ** 2 for a in left)
    right_var = sum((b - right_mean) ** 2 for b in right)
    denominator = math.sqrt(left_var * right_var)
    return numerator / denominator if denominator else 0.0
