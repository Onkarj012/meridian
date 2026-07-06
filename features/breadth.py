from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl

from evidence.validation import (
    ROBUST_MAX_SYMBOL_TRADE_SHARE,
    ROBUST_MIN_VALIDATION_DAYS,
    ROBUST_MIN_VALIDATION_TRADES,
    VALIDATION_END,
    VALIDATION_START,
)


TOP_K_VALUES = (1, 2, 3)


def build_breadth_feasibility_report(gold_path: Path, report_path: Path | None = None) -> dict[str, Any]:
    """Audit whether validation breadth gates are mechanically reachable.

    This intentionally avoids model scores.  It measures the validation decision
    surface in a labeled gold artifact and estimates top-K capacity before any
    candidate selection layer can overfit to it.
    """
    frame = _scan_gold(gold_path)
    if "timestamp" not in frame.collect_schema():
        raise ValueError("breadth audit requires a timestamp column")
    if "symbol" not in frame.collect_schema():
        raise ValueError("breadth audit requires a symbol column")

    validation = _validation_rows(frame)
    validation_rows = validation.select(pl.len()).collect().item()
    if validation_rows == 0:
        raise ValueError(f"no validation rows found between {VALIDATION_START} and {VALIDATION_END}")

    bars_by_timestamp = validation.group_by("timestamp").agg(pl.n_unique("symbol").alias("symbols"))
    bars_by_day = validation.group_by("day").agg(pl.len().alias("rows"), pl.n_unique("symbol").alias("symbols"))
    decision_bar_count = bars_by_timestamp.select(pl.len()).collect().item()
    trading_day_count = bars_by_day.select(pl.len()).collect().item()
    symbol_count = validation.select(pl.n_unique("symbol")).collect().item()
    rows_per_day = _summary_stats(bars_by_day.select("rows").collect()["rows"].to_list())
    symbols_per_bar = _summary_stats(bars_by_timestamp.select("symbols").collect()["symbols"].to_list())

    top_k_capacity: dict[str, Any] = {}
    for top_k in TOP_K_VALUES:
        per_side = bars_by_timestamp.select(pl.min_horizontal(pl.col("symbols"), pl.lit(top_k)).sum()).collect().item()
        both_sides = bars_by_timestamp.select(pl.min_horizontal(pl.col("symbols"), pl.lit(top_k * 2)).sum()).collect().item()
        top_k_capacity[str(top_k)] = {
            "long_only_max_trades": int(per_side),
            "short_only_max_trades": int(per_side),
            "both_sides_max_trades": int(both_sides),
            "production_trade_count_feasible": int(both_sides) >= ROBUST_MIN_VALIDATION_TRADES,
        }

    edge = _edge_positive_summary(validation)
    concentration_floor = 1.0 / symbol_count if symbol_count else 0.0
    production_feasibility = {
        "min_validation_trades": ROBUST_MIN_VALIDATION_TRADES,
        "min_validation_trading_days": ROBUST_MIN_VALIDATION_DAYS,
        "max_symbol_trade_share": ROBUST_MAX_SYMBOL_TRADE_SHARE,
        "validation_days_feasible": trading_day_count >= ROBUST_MIN_VALIDATION_DAYS,
        "uniform_concentration_floor": concentration_floor,
        "concentration_gate_feasible_under_uniform_selection": concentration_floor <= ROBUST_MAX_SYMBOL_TRADE_SHARE,
        "top_k_1_2_3_trade_count_feasible": any(item["production_trade_count_feasible"] for item in top_k_capacity.values()),
    }
    production_feasibility["structurally_feasible_at_observed_breadth"] = bool(
        production_feasibility["validation_days_feasible"]
        and production_feasibility["concentration_gate_feasible_under_uniform_selection"]
        and production_feasibility["top_k_1_2_3_trade_count_feasible"]
    )

    report = {
        "format": "meridian.breadth-feasibility.v1",
        "gold_path": str(gold_path),
        "validation_period": [VALIDATION_START, VALIDATION_END],
        "validation_rows": int(validation_rows),
        "validation_decision_bars": int(decision_bar_count),
        "validation_trading_days": int(trading_day_count),
        "distinct_symbols": int(symbol_count),
        "rows_per_day": rows_per_day,
        "symbols_per_bar": symbols_per_bar,
        "maximum_theoretical_top_k_trades": top_k_capacity,
        "realized_edge_positive_candidates_per_day": edge,
        "concentration_floor_under_uniform_selection": concentration_floor,
        "production_gate_feasibility": production_feasibility,
        "interpretation": _interpretation(production_feasibility),
    }
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return report


def _scan_gold(path: Path) -> pl.LazyFrame:
    if path.suffix == ".parquet":
        return pl.scan_parquet(path)
    if path.suffix == ".csv":
        return pl.scan_csv(path, try_parse_dates=False)
    raise ValueError(f"unsupported gold artifact type: {path.suffix}")


def _validation_rows(frame: pl.LazyFrame) -> pl.LazyFrame:
    timestamp = pl.col("timestamp").cast(pl.Utf8)
    filtered = frame.with_columns(timestamp.str.slice(0, 10).alias("day")).filter(
        (pl.col("day") >= VALIDATION_START) & (pl.col("day") <= VALIDATION_END)
    )
    if "label_excluded" in frame.collect_schema():
        filtered = filtered.filter(pl.col("label_excluded").fill_null(False).not_())
    return filtered


def _edge_positive_summary(validation: pl.LazyFrame) -> dict[str, Any]:
    schema = validation.collect_schema()
    long_expr = pl.col("long_net_return_bps") > 0 if "long_net_return_bps" in schema else pl.lit(False)
    short_expr = pl.col("short_net_return_bps") > 0 if "short_net_return_bps" in schema else pl.lit(False)
    by_day = (
        validation.with_columns(
            long_expr.alias("long_edge_positive"),
            short_expr.alias("short_edge_positive"),
            (long_expr | short_expr).alias("any_edge_positive"),
        )
        .group_by("day")
        .agg(
            pl.sum("long_edge_positive").alias("long"),
            pl.sum("short_edge_positive").alias("short"),
            pl.sum("any_edge_positive").alias("any_side"),
        )
        .collect()
    )
    return {
        "long": _summary_stats(by_day["long"].to_list()),
        "short": _summary_stats(by_day["short"].to_list()),
        "any_side": _summary_stats(by_day["any_side"].to_list()),
    }


def _summary_stats(values: list[int | float]) -> dict[str, float]:
    series = pl.Series(values)
    return {
        "min": float(series.min()),
        "p50": float(series.quantile(0.50, interpolation="nearest")),
        "mean": float(series.mean()),
        "p95": float(series.quantile(0.95, interpolation="nearest")),
        "max": float(series.max()),
    }


def _interpretation(feasibility: dict[str, Any]) -> str:
    if feasibility["structurally_feasible_at_observed_breadth"]:
        return (
            "Production breadth gates are mechanically reachable at the observed validation breadth; "
            "kill-tests should still use the pre-registered research gate until a candidate clears the "
            "full promotion gate."
        )
    return (
        "Production breadth gates are structurally constrained at the observed validation breadth; "
        "do not weaken promotion gates silently. Treat kill-tests as research-gate evidence until "
        "a stronger signal or a point-in-time universe expansion justifies promotion work."
    )
