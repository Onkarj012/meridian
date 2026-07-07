"""Sleeve F real-feed replay evidence helpers."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from evidence.futures_costs import COST_SCENARIOS_BPS, DEFAULT_PRODUCTION_FUTURES_COST_BPS
from evidence.sleeves import DEFAULT_PROMOTION_THRESHOLDS, canonical_report, concentration, futures_promotion_gates_from_metrics, gate_failure_reason, json_safe
from features.sleeve_f import build_sleeve_f_router_features, validate_real_futures_feed
from features.sleeve_f_signals import SESSION_START_MINUTE, build_signal_gate

DEFAULT_FUTURES_ROUND_TRIP_COST_BPS = DEFAULT_PRODUCTION_FUTURES_COST_BPS
DEFAULT_SEALED_TEST_FRACTION = 0.20
DEFAULT_SKIP_FIRST_MINUTES = 15
IST = timezone(timedelta(hours=5, minutes=30))
OUTCOME_BUCKETS = ("target", "stop", "timeout", "eod")


def build_front_month_replay_trades(
    rows: Iterable[Mapping[str, Any]],
    target_bps: float,
    stop_bps: float,
    horizon_bars: int,
    min_realized_vol: float | None = None,
    signal_config: Mapping[str, Any] | None = None,
    allow_overlap: bool = False,
    require_signal_config: bool = False,
    skip_first_minutes: int = DEFAULT_SKIP_FIRST_MINUTES,
    lunch_start_minute: int | str | None = None,
    lunch_end_minute: int | str | None = None,
) -> list[dict[str, Any]]:
    """Replay side-aware first-touch outcomes over front-month futures rows."""
    target = float(target_bps)
    stop = float(stop_bps)
    horizon = int(horizon_bars)
    if target <= 0 or stop <= 0:
        raise ValueError("target_bps and stop_bps must be positive")
    if horizon <= 0:
        raise ValueError("horizon_bars must be positive")
    signal_cfg = _normalized_signal_config(signal_config, require_signal_config=require_signal_config)
    entry_filters = _entry_filters(
        signal_cfg,
        skip_first_minutes=skip_first_minutes,
        lunch_start_minute=lunch_start_minute,
        lunch_end_minute=lunch_end_minute,
    )
    config_side = _signal_direction(signal_cfg)

    config = {
        "target_bps": target,
        "stop_bps": stop,
        "horizon_bars": horizon,
        "min_realized_vol": None if min_realized_vol is None else float(min_realized_vol),
        "signal_config": dict(signal_cfg),
        "allow_overlap": bool(allow_overlap),
        "side": config_side,
        "entry": "next_bar_open_or_signal_close",
        "resolution": "first_touch_stop_before_target_same_bar",
        "mode": "evidence_replay",
        **entry_filters,
    }
    row_list = _front_month_rows([dict(row) for row in rows])
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in row_list:
        grouped[_symbol(row)].append(row)

    trades: list[dict[str, Any]] = []
    for symbol, symbol_rows in grouped.items():
        ordered = sorted(symbol_rows, key=_timestamp)
        signal_values = build_signal_gate(ordered, signal_cfg)
        busy_until_index = -1
        for index, signal in enumerate(ordered):
            if not allow_overlap and index <= busy_until_index:
                continue
            signal_value = signal_values[index]
            if not bool(signal_value.get("gate", False)):
                continue
            if min_realized_vol is not None and _realized_vol(signal) < float(min_realized_vol):
                continue

            signal_date = _trading_date_key(signal)
            path: list[dict[str, Any]] = []
            for future in ordered[index + 1 :]:
                if _trading_date_key(future) != signal_date:
                    break
                path.append(future)
                if len(path) >= horizon:
                    break
            if len(path) < horizon:
                continue
            entry_bar = path[0]
            if not _entry_time_allowed(entry_bar, entry_filters):
                continue
            entry_price = _price(entry_bar.get("open"))
            if entry_price is None:
                entry_price = _price(signal.get("close", signal.get("price")))
            if entry_price is None or entry_price <= 0:
                continue

            side = _signal_direction(signal_value, default=config_side)
            if side == "short":
                target_price = entry_price * (1.0 - target / 10000.0)
                stop_price = entry_price * (1.0 + stop / 10000.0)
            else:
                target_price = entry_price * (1.0 + target / 10000.0)
                stop_price = entry_price * (1.0 - stop / 10000.0)
            outcome = "timeout"
            touch_bar: int | None = None
            exit_bar = path[-1]
            exit_offset = len(path)
            exit_price = _price(exit_bar.get("close", exit_bar.get("price"))) or entry_price
            gross_bps = _gross_return_bps(entry_price, exit_price, side)

            for offset, future in enumerate(path, start=1):
                high = _price(future.get("high", future.get("close", future.get("price"))))
                low = _price(future.get("low", future.get("close", future.get("price"))))
                if high is None or low is None:
                    continue
                if side == "short":
                    stop_touched = high >= stop_price
                    target_touched = low <= target_price
                else:
                    stop_touched = low <= stop_price
                    target_touched = high >= target_price
                if stop_touched:
                    outcome = "stop"
                    touch_bar = offset
                    exit_offset = offset
                    exit_bar = future
                    exit_price = stop_price
                    gross_bps = -stop
                    break
                if target_touched:
                    outcome = "target"
                    touch_bar = offset
                    exit_offset = offset
                    exit_bar = future
                    exit_price = target_price
                    gross_bps = target
                    break

            if not allow_overlap:
                busy_until_index = index + exit_offset

            trades.append(
                {
                    "config": config,
                    "symbol": symbol,
                    "side": side,
                    "signal_timestamp": _timestamp(signal),
                    "entry_timestamp": _timestamp(entry_bar),
                    "exit_timestamp": _timestamp(exit_bar),
                    "entry_price": float(entry_price),
                    "exit_price": float(exit_price),
                    "outcome": outcome,
                    "touch_bar": touch_bar,
                    "gross_bps": float(gross_bps),
                    "outcome_bps": float(gross_bps),
                    "target_bps": target,
                    "stop_bps": stop,
                    "horizon_bars": horizon,
                    "realized_vol": _realized_vol(signal),
                    "signal": signal_value,
                }
            )

    return json_safe(sorted(trades, key=lambda item: (str(item["signal_timestamp"]), str(item["symbol"]))))


def evaluate_replay_trades(trades: Iterable[Mapping[str, Any]], cost_bps: float) -> dict[str, Any]:
    """Evaluate replay trades after a fixed round-trip cost in bps."""
    trade_list = [dict(trade) for trade in trades]
    cost = float(cost_bps)
    gross_returns = [float(_number(trade.get("gross_bps", trade.get("outcome_bps"))) or 0.0) for trade in trade_list]
    net_returns = [gross - cost for gross in gross_returns]
    wins = sum(value > 0 for value in net_returns)
    losses = sum(value < 0 for value in net_returns)
    gross_profit = sum(value for value in net_returns if value > 0)
    gross_loss = abs(sum(value for value in net_returns if value < 0))
    trading_days = len({_date_key(trade) for trade in trade_list if _date_key(trade)})
    outcome_counts = _outcome_counts(trade_list)
    gross_by_outcome = _returns_by_outcome(trade_list, gross_returns)
    net_by_outcome = _returns_by_outcome(trade_list, net_returns)
    yearly_folds = _yearly_folds(trade_list, gross_returns, net_returns)

    result = {
        "trades": len(trade_list),
        "wins": int(wins),
        "losses": int(losses),
        "hit_rate": wins / len(trade_list) if trade_list else 0.0,
        "net_win_rate": wins / len(trade_list) if trade_list else 0.0,
        "touch_rate": outcome_counts["target"] / len(trade_list) if trade_list else 0.0,
        "outcome_counts": outcome_counts,
        "gross_ev_bps": _mean(gross_returns),
        "net_ev_bps": _mean(net_returns),
        "gross_ev_bps_by_outcome": {outcome: _mean(gross_by_outcome[outcome]) for outcome in OUTCOME_BUCKETS},
        "net_ev_bps_by_outcome": {outcome: _mean(net_by_outcome[outcome]) for outcome in OUTCOME_BUCKETS},
        "cost_bps": cost,
        "profit_factor": gross_profit / gross_loss if gross_loss else (gross_profit if gross_profit else 0.0),
        "max_drawdown_bps": _max_drawdown(net_returns),
        "trading_days": trading_days,
        "folds": yearly_folds,
        "yearly_folds": yearly_folds,
        "day_folds": _day_folds(trade_list, gross_returns, net_returns),
        "weekly_folds": _trading_day_bucket_folds(trade_list, gross_returns, net_returns, bucket_size=5),
    }
    return json_safe(result)


def run_sleeve_f_real_feed_evidence(
    rows: Iterable[Mapping[str, Any]],
    grid: Iterable[Mapping[str, Any] | tuple[Any, ...] | list[Any]],
    grid_committed: bool = True,
    fixture_mode: bool = False,
    cost_bps: float = DEFAULT_FUTURES_ROUND_TRIP_COST_BPS,
    cost_scenarios_bps: Iterable[float] = COST_SCENARIOS_BPS,
    allow_overlap: bool = False,
    sealed_test_fraction: float = DEFAULT_SEALED_TEST_FRACTION,
    **promotion_flags: Any,
) -> dict[str, Any]:
    """Run Sleeve F real-feed replay evidence over a committed target grid."""
    row_list = [dict(row) for row in rows]
    grid_list = normalize_replay_grid(grid)
    validation = validate_real_futures_feed(row_list)
    prerequisites = {
        "real_feed_validated": validation["validated"],
        "committed_grid": bool(grid_committed),
        "fixture_mode": bool(fixture_mode),
        "grid_candidates": len(grid_list),
        "allow_overlap": bool(allow_overlap),
        "sealed_test_fraction": float(sealed_test_fraction),
        "production_cost_bps": float(cost_bps),
        "cost_scenarios_bps": [float(value) for value in cost_scenarios_bps],
    }
    if not fixture_mode:
        _require_grid_signal_configs(grid_list)
    if not validation["validated"]:
        return canonical_report(
            sleeve="F",
            phase=2,
            prerequisites=prerequisites,
            gates={},
            candidate=None,
            reason="real_futures_feed_validation_failed",
            extra={"feed_validation": validation},
        )
    if not grid_committed:
        return canonical_report(
            sleeve="F",
            phase=2,
            prerequisites=prerequisites,
            gates={},
            candidate=None,
            reason="walkforward_grid_not_committed",
            extra={"feed_validation": validation},
        )
    if not grid_list:
        return canonical_report(
            sleeve="F",
            phase=2,
            prerequisites=prerequisites,
            gates={},
            candidate=None,
            reason="replay_grid_empty",
            extra={"feed_validation": validation},
        )

    features = build_sleeve_f_router_features(row_list)
    validation_rows, sealed_rows, split = _split_rows_by_trading_days(features, sealed_test_fraction)
    prerequisites.update(
        {
            "validation_trading_days": split["validation_trading_days"],
            "sealed_test_trading_days": split["sealed_test_trading_days"],
        }
    )
    candidates = [
        _evaluate_config(
            features=validation_rows,
            config=config,
            cost_bps=cost_bps,
            cost_scenarios_bps=cost_scenarios_bps,
            promotion_flags=promotion_flags,
            allow_overlap=allow_overlap,
            require_signal_config=not fixture_mode,
        )
        for config in grid_list
    ]
    candidate = select_replay_candidate(candidates)
    sealed_test = _evaluate_sealed_test(
        rows=sealed_rows,
        selected_config=dict(candidate["grid_config"]),
        cost_bps=cost_bps,
        cost_scenarios_bps=cost_scenarios_bps,
        promotion_flags=promotion_flags,
        allow_overlap=allow_overlap,
        require_signal_config=not fixture_mode,
        split=split,
    )
    candidate = {
        **candidate,
        "sealed_test": sealed_test,
        "sealed_test_passed": bool(sealed_test.get("passed", False)),
    }
    gates = futures_promotion_gates_from_metrics(
        trades=candidate["trades"],
        trading_days=candidate["trading_days"],
        positive_fold_share=candidate["positive_fold_share"],
        worst_fold_bps=candidate["worst_fold_bps"],
        ci_low_bps=candidate["ci_low_bps"],
        dsr=candidate["dsr"],
        sealed_test_passed=candidate["sealed_test_passed"],
        fixture_mode=fixture_mode,
    )
    promoted = all(gates.values()) and validation["validated"] and bool(grid_committed) and bool(grid_list)
    reason = None if promoted else gate_failure_reason(gates, "sleeve_f_real_feed_not_promoted")
    return canonical_report(
        sleeve="F",
        phase=2,
        prerequisites=prerequisites,
        gates=gates,
        candidate=candidate,
        promoted=promoted,
        reason=reason,
        extra={
            "feed_validation": validation,
            "metrics": candidate["metrics"],
            "cost_scenarios": candidate["cost_scenarios"],
            "stress_survives_5bps": candidate["stress_survives_5bps"],
            "router_features": {"rows": len(features)},
            "walkforward_split": split,
            "sealed_test": sealed_test,
            "config_summaries": candidates,
        },
    )


def _evaluate_config(
    features: list[dict[str, Any]],
    config: Mapping[str, Any],
    *,
    cost_bps: float,
    cost_scenarios_bps: Iterable[float],
    promotion_flags: Mapping[str, Any],
    allow_overlap: bool = False,
    require_signal_config: bool = False,
) -> dict[str, Any]:
    signal_cfg = dict(config.get("signal_config") or {})
    trades = build_front_month_replay_trades(
        features,
        target_bps=float(config["target_bps"]),
        stop_bps=float(config["stop_bps"]),
        horizon_bars=int(config["horizon_bars"]),
        min_realized_vol=config.get("min_realized_vol"),
        signal_config=signal_cfg,
        allow_overlap=allow_overlap,
        require_signal_config=require_signal_config,
        skip_first_minutes=int(config.get("skip_first_minutes", signal_cfg.get("skip_first_minutes", DEFAULT_SKIP_FIRST_MINUTES)) or 0),
        lunch_start_minute=config.get("lunch_start_minute", signal_cfg.get("lunch_start_minute")),
        lunch_end_minute=config.get("lunch_end_minute", signal_cfg.get("lunch_end_minute")),
    )
    return summarize_replay_candidate(trades, config, cost_bps=cost_bps, cost_scenarios_bps=cost_scenarios_bps, promotion_flags=promotion_flags)


def summarize_replay_candidate(
    trades: Iterable[Mapping[str, Any]],
    config: Mapping[str, Any],
    *,
    cost_bps: float,
    cost_scenarios_bps: Iterable[float] = COST_SCENARIOS_BPS,
    promotion_flags: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarize one pre-committed replay config from already-built trades."""
    trade_list = [dict(trade) for trade in trades]
    metrics = evaluate_replay_trades(trade_list, cost_bps=cost_bps)
    selection_folds = _selection_folds(metrics)
    fold_values = [float(fold.get("net_ev_bps", 0.0) or 0.0) for fold in selection_folds]
    worst_fold = min(fold_values) if fold_values else float(metrics["net_ev_bps"])
    positive_fold_share = sum(value > 0 for value in fold_values) / len(fold_values) if fold_values else (1.0 if float(metrics["net_ev_bps"]) > 0 else 0.0)
    conc = concentration(trade.get("symbol") for trade in trade_list)
    flags = promotion_flags or {}
    beats_baselines = bool(
        flags.get(
            "beats_baselines",
            flags.get("baseline_passed", flags.get("baselines_passed", flags.get("beats_rung0_baselines_net_sharpe", False))),
        )
    )
    dsr = float(flags.get("dsr", flags.get("deflated_sharpe_ratio", 0.0)) or 0.0)
    cost_scenarios = _cost_scenario_summaries(trade_list, cost_scenarios_bps)
    stress_survives_5bps = bool(cost_scenarios.get("5.0", {}).get("ci_low_bps", 0.0) > 0.0)
    return {
        "config_id": str(config.get("id", config.get("name", "config"))),
        "model_family": "front_month_first_touch_replay",
        "grid_config": json_safe(dict(config)),
        "target_bps": float(config["target_bps"]),
        "stop_bps": float(config["stop_bps"]),
        "horizon_bars": int(config["horizon_bars"]),
        "min_realized_vol": config.get("min_realized_vol"),
        "signal_config": json_safe(dict(config.get("signal_config") or {})),
        "skip_first_minutes": int(config.get("skip_first_minutes", dict(config.get("signal_config") or {}).get("skip_first_minutes", DEFAULT_SKIP_FIRST_MINUTES)) or 0),
        "lunch_start_minute": config.get("lunch_start_minute", dict(config.get("signal_config") or {}).get("lunch_start_minute")),
        "lunch_end_minute": config.get("lunch_end_minute", dict(config.get("signal_config") or {}).get("lunch_end_minute")),
        "trades": int(metrics["trades"]),
        "wins": int(metrics["wins"]),
        "losses": int(metrics["losses"]),
        "trading_days": int(metrics["trading_days"]),
        "hit_rate": float(metrics["hit_rate"]),
        "net_win_rate": float(metrics["net_win_rate"]),
        "touch_rate": float(metrics["touch_rate"]),
        "outcome_counts": metrics["outcome_counts"],
        "gross_ev_bps": float(metrics["gross_ev_bps"]),
        "net_ev_bps": float(metrics["net_ev_bps"]),
        "expected_value_bps": float(metrics["net_ev_bps"]),
        "gross_ev_bps_by_outcome": metrics["gross_ev_bps_by_outcome"],
        "net_ev_bps_by_outcome": metrics["net_ev_bps_by_outcome"],
        "profit_factor": float(metrics["profit_factor"]),
        "max_drawdown_bps": float(metrics["max_drawdown_bps"]),
        "positive_fold_share": float(positive_fold_share),
        "worst_fold_bps": float(worst_fold),
        "ci_low_bps": min(float(metrics["net_ev_bps"]), float(worst_fold)),
        "distinct_symbols": int(conc["distinct_symbols"]),
        "max_symbol_trade_share": float(conc["max_symbol_trade_share"]),
        "concentration": conc,
        "folds": metrics["folds"],
        "yearly_folds": metrics["yearly_folds"],
        "day_folds": metrics["day_folds"],
        "weekly_folds": metrics["weekly_folds"],
        "selection_folds": selection_folds,
        "metrics": metrics,
        "cost_bps": float(cost_bps),
        "production_cost_bps": float(cost_bps),
        "cost_scenarios": cost_scenarios,
        "stress_survives_5bps": stress_survives_5bps,
        "beats_baselines": beats_baselines,
        "dsr": dsr,
        "sealed_test_passed": False,
    }


def normalize_replay_grid(grid: Iterable[Mapping[str, Any] | tuple[Any, ...] | list[Any]]) -> list[dict[str, Any]]:
    """Normalize replay grid entries while preserving tuple backward compatibility."""
    return [_normalize_grid_config(item, index) for index, item in enumerate(grid or [])]


def select_replay_candidate(candidates: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Select a candidate using validation folds only."""
    candidate_list = [dict(candidate) for candidate in candidates]
    if not candidate_list:
        raise ValueError("replay candidate list is empty")
    return max(candidate_list, key=lambda item: (item["worst_fold_bps"], item["net_ev_bps"], item["trades"], item["config_id"]))


def _evaluate_sealed_test(
    *,
    rows: list[dict[str, Any]],
    selected_config: Mapping[str, Any],
    cost_bps: float,
    cost_scenarios_bps: Iterable[float],
    promotion_flags: Mapping[str, Any],
    allow_overlap: bool,
    require_signal_config: bool,
    split: Mapping[str, Any],
) -> dict[str, Any]:
    if not rows:
        return {
            "skipped": True,
            "passed": False,
            "reason": "sealed_test_split_empty",
            "trading_days": 0,
            "fraction": split.get("sealed_test_fraction", DEFAULT_SEALED_TEST_FRACTION),
            "metrics": evaluate_replay_trades([], cost_bps=cost_bps),
            "cost_scenarios": _cost_scenario_summaries([], cost_scenarios_bps),
        }
    sealed_summary = _evaluate_config(
        features=rows,
        config=selected_config,
        cost_bps=cost_bps,
        cost_scenarios_bps=cost_scenarios_bps,
        promotion_flags=promotion_flags,
        allow_overlap=allow_overlap,
        require_signal_config=require_signal_config,
    )
    gate_floor = float(DEFAULT_PROMOTION_THRESHOLDS.worst_fold_floor_bps)
    passed = bool(sealed_summary["trades"] > 0 and sealed_summary["net_ev_bps"] > 0.0 and sealed_summary["worst_fold_bps"] > gate_floor)
    return {
        "skipped": False,
        "passed": passed,
        "config_id": sealed_summary["config_id"],
        "trading_days": sealed_summary["trading_days"],
        "fraction": split.get("sealed_test_fraction", DEFAULT_SEALED_TEST_FRACTION),
        "net_ev_bps": sealed_summary["net_ev_bps"],
        "worst_fold_bps": sealed_summary["worst_fold_bps"],
        "touch_rate": sealed_summary["touch_rate"],
        "outcome_counts": sealed_summary["outcome_counts"],
        "metrics": sealed_summary["metrics"],
        "folds": sealed_summary["selection_folds"],
        "cost_scenarios": sealed_summary["cost_scenarios"],
        "stress_survives_5bps": sealed_summary["stress_survives_5bps"],
    }


def _split_rows_by_trading_days(rows: list[dict[str, Any]], sealed_test_fraction: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    fraction = max(0.0, min(1.0, float(sealed_test_fraction)))
    days = sorted({_trading_date_key(row) for row in rows if _trading_date_key(row)})
    if len(days) < 2 or fraction <= 0.0:
        split = {
            "sealed_test_fraction": fraction,
            "trading_days": len(days),
            "validation_trading_days": len(days),
            "sealed_test_trading_days": 0,
            "validation_start": days[0] if days else None,
            "validation_end": days[-1] if days else None,
            "sealed_test_start": None,
            "sealed_test_end": None,
        }
        return list(rows), [], split
    sealed_count = max(1, math.ceil(len(days) * fraction))
    if sealed_count >= len(days):
        sealed_count = len(days) - 1
    sealed_days = set(days[-sealed_count:])
    validation_rows = [row for row in rows if _trading_date_key(row) not in sealed_days]
    sealed_rows = [row for row in rows if _trading_date_key(row) in sealed_days]
    validation_days = [day for day in days if day not in sealed_days]
    sealed_day_list = [day for day in days if day in sealed_days]
    split = {
        "sealed_test_fraction": fraction,
        "trading_days": len(days),
        "validation_trading_days": len(validation_days),
        "sealed_test_trading_days": len(sealed_day_list),
        "validation_start": validation_days[0] if validation_days else None,
        "validation_end": validation_days[-1] if validation_days else None,
        "sealed_test_start": sealed_day_list[0] if sealed_day_list else None,
        "sealed_test_end": sealed_day_list[-1] if sealed_day_list else None,
    }
    return validation_rows, sealed_rows, split


def _selection_folds(metrics: Mapping[str, Any]) -> list[dict[str, Any]]:
    for key in ("weekly_folds", "day_folds", "folds"):
        folds = [dict(item) for item in (metrics.get(key) or [])]
        if len(folds) >= 2:
            return folds
    return [dict(item) for item in (metrics.get("folds") or [])]


def _require_grid_signal_configs(grid: Iterable[Mapping[str, Any]]) -> None:
    for config in grid:
        _normalized_signal_config(config.get("signal_config"), require_signal_config=True)


def _normalized_signal_config(signal_config: Mapping[str, Any] | None, *, require_signal_config: bool) -> dict[str, Any]:
    config = dict(signal_config or {})
    if require_signal_config and not config:
        raise ValueError("signal_config required for non-fixture Sleeve F replay")
    return config


def _entry_filters(
    signal_config: Mapping[str, Any],
    *,
    skip_first_minutes: int,
    lunch_start_minute: int | str | None,
    lunch_end_minute: int | str | None,
) -> dict[str, Any]:
    skip_minutes = int(signal_config.get("skip_first_minutes", skip_first_minutes) or 0)
    if skip_minutes < 0:
        raise ValueError("skip_first_minutes must be >= 0")
    lunch_start = _minute_config(lunch_start_minute)
    lunch_end = _minute_config(lunch_end_minute)
    if (lunch_start is None) != (lunch_end is None):
        raise ValueError("both lunch_start_minute and lunch_end_minute are required for the lunch block")
    if lunch_start is not None and lunch_end is not None and lunch_end < lunch_start:
        raise ValueError("lunch_end_minute must be >= lunch_start_minute")
    return {
        "skip_first_minutes": skip_minutes,
        "lunch_start_minute": lunch_start,
        "lunch_end_minute": lunch_end,
    }


def _entry_time_allowed(entry_bar: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
    minute = _minute_of_day(_timestamp(entry_bar))
    min_entry_minute = SESSION_START_MINUTE + int(filters.get("skip_first_minutes", DEFAULT_SKIP_FIRST_MINUTES) or 0)
    if minute < min_entry_minute:
        return False
    lunch_start = filters.get("lunch_start_minute")
    lunch_end = filters.get("lunch_end_minute")
    if lunch_start is not None and lunch_end is not None and int(lunch_start) <= minute <= int(lunch_end):
        return False
    return True


def _signal_direction(config: Mapping[str, Any], *, default: str = "long") -> str:
    direction = str(config.get("direction", default) or default).lower()
    if direction not in {"long", "short"}:
        raise ValueError("Sleeve F replay direction must be 'long' or 'short'")
    return direction


def _gross_return_bps(entry_price: float, exit_price: float, side: str) -> float:
    if side == "short":
        return (entry_price - exit_price) / entry_price * 10000.0
    return (exit_price / entry_price - 1.0) * 10000.0


def _cost_scenario_summaries(trades: Iterable[Mapping[str, Any]], cost_scenarios_bps: Iterable[float]) -> dict[str, dict[str, Any]]:
    trade_list = [dict(trade) for trade in trades]
    scenarios: dict[str, dict[str, Any]] = {}
    for cost in cost_scenarios_bps:
        metrics = evaluate_replay_trades(trade_list, cost_bps=float(cost))
        folds = _selection_folds(metrics)
        fold_values = [float(fold.get("net_ev_bps", 0.0) or 0.0) for fold in folds]
        worst_fold = min(fold_values) if fold_values else float(metrics["net_ev_bps"])
        ci_low = min(float(metrics["net_ev_bps"]), float(worst_fold))
        scenarios[f"{float(cost):.1f}"] = {
            "cost_bps": float(cost),
            "trades": int(metrics["trades"]),
            "net_ev_bps": float(metrics["net_ev_bps"]),
            "ci_low_bps": float(ci_low),
            "worst_fold_bps": float(worst_fold),
        }
    return json_safe(scenarios)


def _minute_config(value: int | str | None) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return value
    text = str(value)
    if text.isdigit():
        return int(text)
    if len(text) >= 5 and text[0:2].isdigit() and text[3:5].isdigit():
        return int(text[0:2]) * 60 + int(text[3:5])
    raise ValueError(f"invalid minute config: {value}")


def _minute_of_day(value: Any) -> int:
    if isinstance(value, datetime):
        return value.hour * 60 + value.minute
    text = str(value or "")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(IST)
        return parsed.hour * 60 + parsed.minute
    except ValueError:
        pass
    if len(text) >= 16 and text[11:13].isdigit() and text[14:16].isdigit():
        return int(text[11:13]) * 60 + int(text[14:16])
    return 0


def _normalize_grid_config(item: Mapping[str, Any] | tuple[Any, ...] | list[Any], index: int) -> dict[str, Any]:
    if isinstance(item, Mapping):
        config = dict(item)
    else:
        values = list(item)
        if len(values) < 3:
            raise ValueError("grid tuple items must include target_bps, stop_bps, horizon_bars")
        config = {"target_bps": values[0], "stop_bps": values[1], "horizon_bars": values[2]}
        if len(values) > 3:
            config["min_realized_vol"] = values[3]
    missing = [key for key in ("target_bps", "stop_bps", "horizon_bars") if key not in config]
    if missing:
        raise ValueError(f"grid item missing required keys: {', '.join(missing)}")
    config.setdefault("id", f"grid_{index}")
    return config


def _front_month_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    front = [row for row in rows if _is_front_month_symbol(_symbol(row))]
    return front if front else rows


def _is_front_month_symbol(symbol: str) -> bool:
    return _clean_symbol(symbol).endswith("-I")


def _symbol(row: Mapping[str, Any]) -> str:
    return _clean_symbol(row.get("tradingsymbol", row.get("symbol", "")))


def _clean_symbol(value: Any) -> str:
    return " ".join(str(value or "").upper().strip().split())


def _timestamp(row: Mapping[str, Any]) -> str:
    if row.get("timestamp") not in (None, ""):
        return str(row.get("timestamp"))
    if row.get("datetime") not in (None, ""):
        return str(row.get("datetime"))
    if row.get("date") not in (None, "") and row.get("time") not in (None, ""):
        return f"{row.get('date')}T{row.get('time')}"
    return str(row.get("date", ""))


def _date_key(row: Mapping[str, Any]) -> str | None:
    for key in ("signal_timestamp", "entry_timestamp", "exit_timestamp", "timestamp", "date"):
        value = row.get(key)
        if value not in (None, ""):
            return _date_from_value(value)
    return None


def _trading_date_key(row: Mapping[str, Any]) -> str:
    return _date_from_value(_timestamp(row)) or ""


def _date_from_value(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) == 10 and text[4:5] == "-" and text[7:8] == "-":
        return text
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=IST)
        else:
            parsed = parsed.astimezone(IST)
        return parsed.date().isoformat()
    except ValueError:
        pass
    return text[:10] if len(text) >= 10 else text


def _realized_vol(row: Mapping[str, Any]) -> float:
    if row.get("realized_vol") is not None:
        return float(_number(row.get("realized_vol")) or 0.0)
    for key, value in row.items():
        if str(key).startswith("realized_vol_"):
            return float(_number(value) or 0.0)
    return 0.0


def _price(value: Any) -> float | None:
    result = _number(value)
    return result if result is not None and result > 0 else None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _mean(values: Iterable[float]) -> float:
    data = list(values)
    return sum(data) / len(data) if data else 0.0


def _outcome_counts(trades: list[dict[str, Any]]) -> dict[str, int]:
    counts = {outcome: 0 for outcome in OUTCOME_BUCKETS}
    for trade in trades:
        outcome = str(trade.get("outcome", "timeout"))
        if outcome in counts:
            counts[outcome] += 1
    return counts


def _returns_by_outcome(trades: list[dict[str, Any]], returns: list[float]) -> dict[str, list[float]]:
    grouped = {outcome: [] for outcome in OUTCOME_BUCKETS}
    for trade, value in zip(trades, returns):
        outcome = str(trade.get("outcome", "timeout"))
        if outcome in grouped:
            grouped[outcome].append(float(value))
    return grouped


def _max_drawdown(values: Iterable[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for value in values:
        equity += float(value)
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return drawdown


def _yearly_folds(trades: list[dict[str, Any]], gross_returns: list[float], net_returns: list[float]) -> list[dict[str, Any]]:
    by_year: dict[str, list[tuple[dict[str, Any], float, float]]] = defaultdict(list)
    for trade, gross, net in zip(trades, gross_returns, net_returns):
        date_key = _date_key(trade)
        if date_key and len(date_key) >= 4 and date_key[:4].isdigit():
            by_year[date_key[:4]].append((trade, gross, net))
    folds = []
    for year, items in sorted(by_year.items()):
        fold = _fold_summary(year, items)
        fold["year"] = int(year)
        folds.append(fold)
    return folds


def _day_folds(trades: list[dict[str, Any]], gross_returns: list[float], net_returns: list[float]) -> list[dict[str, Any]]:
    by_day: dict[str, list[tuple[dict[str, Any], float, float]]] = defaultdict(list)
    for trade, gross, net in zip(trades, gross_returns, net_returns):
        date_key = _date_key(trade)
        if date_key:
            by_day[date_key].append((trade, gross, net))
    return [_fold_summary(day, items) for day, items in sorted(by_day.items())]


def _trading_day_bucket_folds(
    trades: list[dict[str, Any]],
    gross_returns: list[float],
    net_returns: list[float],
    *,
    bucket_size: int,
) -> list[dict[str, Any]]:
    days = sorted({_date_key(trade) for trade in trades if _date_key(trade)})
    if not days:
        return []
    day_position = {day: index for index, day in enumerate(days)}
    by_bucket: dict[int, list[tuple[dict[str, Any], float, float]]] = defaultdict(list)
    for trade, gross, net in zip(trades, gross_returns, net_returns):
        day = _date_key(trade)
        if day is None:
            continue
        by_bucket[day_position[day] // max(1, int(bucket_size))].append((trade, gross, net))
    folds = []
    for bucket, items in sorted(by_bucket.items()):
        bucket_days = sorted({_date_key(item[0]) for item in items if _date_key(item[0])})
        name = f"{bucket_days[0]}..{bucket_days[-1]}" if bucket_days else f"bucket_{bucket}"
        fold = _fold_summary(name, items)
        fold["bucket"] = int(bucket)
        folds.append(fold)
    return folds


def _fold_summary(name: str, items: list[tuple[dict[str, Any], float, float]]) -> dict[str, Any]:
    gross = [item[1] for item in items]
    net = [item[2] for item in items]
    wins = sum(value > 0 for value in net)
    losses = sum(value < 0 for value in net)
    trades = [item[0] for item in items]
    outcome_counts = _outcome_counts(trades)
    return {
        "fold": name,
        "trades": len(items),
        "wins": int(wins),
        "losses": int(losses),
        "hit_rate": wins / len(items) if items else 0.0,
        "net_win_rate": wins / len(items) if items else 0.0,
        "touch_rate": outcome_counts["target"] / len(items) if items else 0.0,
        "outcome_counts": outcome_counts,
        "gross_ev_bps": _mean(gross),
        "net_ev_bps": _mean(net),
        "net_return_bps": sum(net),
        "trading_days": len({_date_key(item[0]) for item in items if _date_key(item[0])}),
    }
