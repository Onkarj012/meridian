"""Run Sleeve F real-data replay grid over OptiNet NIFTY futures files."""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evidence.sleeve_f_real import (  # noqa: E402
    DEFAULT_FUTURES_ROUND_TRIP_COST_BPS,
    DEFAULT_SEALED_TEST_FRACTION,
    build_front_month_replay_trades,
    normalize_replay_grid,
    select_replay_candidate,
    summarize_replay_candidate,
)
from evidence.futures_costs import COST_SCENARIOS_BPS  # noqa: E402
from evidence.sleeves import DEFAULT_PROMOTION_THRESHOLDS, canonical_report, futures_promotion_gates_from_metrics, gate_failure_reason  # noqa: E402
from features.sleeve_f import build_sleeve_f_router_features, validate_real_futures_feed  # noqa: E402
from ingest.optinet_data import iter_nifty_futures_minute_file, validate_for_meridian_futures  # noqa: E402

DEFAULT_DATA_ROOT = Path("/Users/onkarj012/Projects/market/intranet_optinet/data")
DEFAULT_GRID_PATH = Path("configs/sleeve_f_grid.json")
DEFAULT_OUTPUT = Path("runs/optinet-sleeve-f-full/summary.json")


@dataclass
class QualityAccumulator:
    rows_loaded: int = 0
    zero_volume_count: int = 0
    invalid_session_count: int = 0
    start: str | None = None
    end: str | None = None
    symbols: set[str] | None = None

    def update(self, rows: Iterable[Mapping[str, Any]]) -> None:
        if self.symbols is None:
            self.symbols = set()
        for row in rows:
            self.rows_loaded += 1
            if row.get("symbol") is not None:
                self.symbols.add(str(row["symbol"]))
            if float(row.get("volume") or 0.0) == 0.0:
                self.zero_volume_count += 1
            timestamp = str(row.get("timestamp", row.get("date", "")))
            if timestamp:
                self.start = timestamp if self.start is None else min(self.start, timestamp)
                self.end = timestamp if self.end is None else max(self.end, timestamp)
            minute = _minute_of_day(timestamp)
            if minute < 9 * 60 + 15 or minute > 15 * 60 + 30:
                self.invalid_session_count += 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "row_count": self.rows_loaded,
            "start": self.start,
            "end": self.end,
            "zero_volume_count": self.zero_volume_count,
            "zero_volume_share": self.zero_volume_count / self.rows_loaded if self.rows_loaded else 0.0,
            "invalid_session_count": self.invalid_session_count,
            "symbols": sorted(self.symbols or []),
        }


def run(
    *,
    data_root: str | Path = DEFAULT_DATA_ROOT,
    start: str | None = None,
    end: str | None = None,
    grid: str | Path = DEFAULT_GRID_PATH,
    cost_bps: float = DEFAULT_FUTURES_ROUND_TRIP_COST_BPS,
    cost_scenarios_bps: Iterable[float] = COST_SCENARIOS_BPS,
    allow_overlap: bool = False,
    limit_rows: int | None = None,
    output: str | Path = DEFAULT_OUTPUT,
) -> dict[str, Any]:
    root = Path(data_root)
    grid_path = Path(grid)
    output_path = Path(output)
    grid_configs = normalize_replay_grid(_load_grid(grid_path))
    if not grid_configs:
        raise ValueError("grid must contain at least one config")
    _require_signal_configs(grid_configs)
    if limit_rows is not None and int(limit_rows) <= 0:
        raise ValueError("limit_rows must be positive when provided")

    files = _discover_nifty_futures_files(root, start=start, end=end)
    if not files:
        raise ValueError(f"no NIFTY futures CSV files found under {root} for requested range")
    validation_files, sealed_files, split = _split_files(files, DEFAULT_SEALED_TEST_FRACTION)

    quality = QualityAccumulator()
    validation_trades = {str(config["id"]): [] for config in grid_configs}
    rows_remaining = int(limit_rows) if limit_rows is not None else None
    source_files_used: list[str] = []

    for path in validation_files:
        rows, rows_remaining = _load_rows(path, rows_remaining)
        if not rows:
            continue
        source_files_used.append(str(path))
        quality.update(rows)
        _validate_rows(rows, path)
        features = build_sleeve_f_router_features(rows)
        for config in grid_configs:
            trades = build_front_month_replay_trades(
                features,
                target_bps=float(config["target_bps"]),
                stop_bps=float(config["stop_bps"]),
                horizon_bars=int(config["horizon_bars"]),
                min_realized_vol=config.get("min_realized_vol"),
                signal_config=config.get("signal_config"),
                allow_overlap=allow_overlap,
                require_signal_config=True,
                skip_first_minutes=int(config.get("skip_first_minutes", dict(config.get("signal_config") or {}).get("skip_first_minutes", 15)) or 0),
                lunch_start_minute=config.get("lunch_start_minute", dict(config.get("signal_config") or {}).get("lunch_start_minute")),
                lunch_end_minute=config.get("lunch_end_minute", dict(config.get("signal_config") or {}).get("lunch_end_minute")),
            )
            validation_trades[str(config["id"])].extend(trades)
        if rows_remaining == 0:
            break

    config_summaries = [
        summarize_replay_candidate(validation_trades[str(config["id"])], config, cost_bps=cost_bps, cost_scenarios_bps=cost_scenarios_bps)
        for config in grid_configs
    ]
    selected = select_replay_candidate(config_summaries)

    sealed_trades: list[dict[str, Any]] = []
    selected_config = dict(selected["grid_config"])
    if rows_remaining != 0:
        for path in sealed_files:
            rows, rows_remaining = _load_rows(path, rows_remaining)
            if not rows:
                continue
            source_files_used.append(str(path))
            quality.update(rows)
            _validate_rows(rows, path)
            features = build_sleeve_f_router_features(rows)
            sealed_trades.extend(
                build_front_month_replay_trades(
                    features,
                    target_bps=float(selected_config["target_bps"]),
                    stop_bps=float(selected_config["stop_bps"]),
                    horizon_bars=int(selected_config["horizon_bars"]),
                    min_realized_vol=selected_config.get("min_realized_vol"),
                    signal_config=selected_config.get("signal_config"),
                    allow_overlap=allow_overlap,
                    require_signal_config=True,
                    skip_first_minutes=int(
                        selected_config.get("skip_first_minutes", dict(selected_config.get("signal_config") or {}).get("skip_first_minutes", 15)) or 0
                    ),
                    lunch_start_minute=selected_config.get("lunch_start_minute", dict(selected_config.get("signal_config") or {}).get("lunch_start_minute")),
                    lunch_end_minute=selected_config.get("lunch_end_minute", dict(selected_config.get("signal_config") or {}).get("lunch_end_minute")),
                )
            )
            if rows_remaining == 0:
                break

    sealed_summary = summarize_replay_candidate(sealed_trades, selected_config, cost_bps=cost_bps, cost_scenarios_bps=cost_scenarios_bps)
    sealed_test = _sealed_test_from_summary(sealed_summary, split)
    candidate = {**selected, "sealed_test": sealed_test, "sealed_test_passed": bool(sealed_test["passed"])}
    gates = futures_promotion_gates_from_metrics(
        trades=candidate["trades"],
        trading_days=candidate["trading_days"],
        positive_fold_share=candidate["positive_fold_share"],
        worst_fold_bps=candidate["worst_fold_bps"],
        ci_low_bps=candidate["ci_low_bps"],
        dsr=candidate["dsr"],
        sealed_test_passed=candidate["sealed_test_passed"],
        fixture_mode=False,
    )
    promoted = all(gates.values())
    report = canonical_report(
        sleeve="F",
        phase=2,
        prerequisites={
            "real_feed_validated": True,
            "committed_grid": True,
            "fixture_mode": False,
            "grid_candidates": len(grid_configs),
            "allow_overlap": bool(allow_overlap),
            "production_cost_bps": float(cost_bps),
            "cost_scenarios_bps": [float(value) for value in cost_scenarios_bps],
            "validation_trading_days": split["validation_trading_days"],
            "sealed_test_trading_days": split["sealed_test_trading_days"],
        },
        gates=gates,
        candidate=candidate,
        promoted=promoted,
        reason=None if promoted else gate_failure_reason(gates, "sleeve_f_real_feed_not_promoted"),
        extra={
            "metrics": candidate["metrics"],
            "cost_scenarios": candidate["cost_scenarios"],
            "stress_survives_5bps": candidate["stress_survives_5bps"],
            "sealed_test": sealed_test,
            "config_summaries": config_summaries,
            "walkforward_split": split,
        },
    )

    summary = {
        "data_root": str(root),
        "grid_path": str(grid_path),
        "output": str(output_path),
        "start": start,
        "end": end,
        "cost_bps": float(cost_bps),
        "cost_scenarios_bps": [float(value) for value in cost_scenarios_bps],
        "allow_overlap": bool(allow_overlap),
        "limit_rows": limit_rows,
        "rows_loaded": quality.rows_loaded,
        "source_files_considered": len(files),
        "source_files_used": source_files_used,
        "normalization_quality": quality.as_dict(),
        "walkforward_split": split,
        "outcome_counts": candidate["outcome_counts"],
        "touch_rate": candidate["touch_rate"],
        "sealed_test": sealed_test,
        "cost_scenarios": candidate["cost_scenarios"],
        "stress_survives_5bps": candidate["stress_survives_5bps"],
        "config_summaries": config_summaries,
        "report": report,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def main(argv: Sequence[str] | None = None) -> dict[str, Any]:
    args = _parser().parse_args(argv)
    summary = run(
        data_root=args.data_root,
        start=args.start,
        end=args.end,
        grid=args.grid,
        cost_bps=args.cost_bps,
        allow_overlap=args.allow_overlap,
        limit_rows=args.limit_rows,
        output=args.output,
    )
    print(json.dumps(_console_summary(summary), sort_keys=True))
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    parser.add_argument("--start", help="Inclusive YYYY-MM-DD file date.")
    parser.add_argument("--end", help="Inclusive YYYY-MM-DD file date.")
    parser.add_argument("--grid", default=str(DEFAULT_GRID_PATH), help="Path to JSON list of replay grid entries.")
    parser.add_argument("--cost-bps", type=float, default=DEFAULT_FUTURES_ROUND_TRIP_COST_BPS)
    parser.add_argument("--allow-overlap", action="store_true", default=False)
    parser.add_argument("--limit-rows", type=int)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    return parser


def _load_grid(path: Path) -> list[dict[str, Any]]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, list):
        raise ValueError(f"grid JSON must be a list: {path}")
    return [dict(item) for item in parsed]


def _require_signal_configs(grid: Iterable[Mapping[str, Any]]) -> None:
    for config in grid:
        if not dict(config.get("signal_config") or {}):
            raise ValueError("signal_config required for non-fixture Sleeve F replay")


def _discover_nifty_futures_files(data_root: Path, *, start: str | None, end: str | None) -> list[Path]:
    root = data_root / "option_data" / "nifty_data" / "nifty_fut"
    if not root.exists():
        raise FileNotFoundError(f"NIFTY futures directory not found: {root}")
    start_date = _parse_date(start) if start else None
    end_date = _parse_date(end) if end else None
    files = []
    for path in root.rglob("*.csv"):
        file_date = _file_date(path)
        if file_date is None:
            continue
        if start_date and file_date < start_date:
            continue
        if end_date and file_date > end_date:
            continue
        files.append(path)
    return sorted(files, key=lambda item: (_file_date(item) or date.min, str(item)))


def _split_files(files: list[Path], fraction: float) -> tuple[list[Path], list[Path], dict[str, Any]]:
    days = sorted({_file_date(path) for path in files if _file_date(path) is not None})
    if len(days) < 2:
        return files, [], _split_dict(days, [], fraction)
    sealed_count = max(1, math.ceil(len(days) * fraction))
    if sealed_count >= len(days):
        sealed_count = len(days) - 1
    sealed_days = set(days[-sealed_count:])
    validation_files = [path for path in files if _file_date(path) not in sealed_days]
    sealed_files = [path for path in files if _file_date(path) in sealed_days]
    validation_days = [day for day in days if day not in sealed_days]
    sealed_day_list = [day for day in days if day in sealed_days]
    return validation_files, sealed_files, _split_dict(validation_days, sealed_day_list, fraction)


def _split_dict(validation_days: list[date], sealed_days: list[date], fraction: float) -> dict[str, Any]:
    all_days = validation_days + sealed_days
    return {
        "sealed_test_fraction": float(fraction),
        "trading_days": len(all_days),
        "validation_trading_days": len(validation_days),
        "sealed_test_trading_days": len(sealed_days),
        "validation_start": validation_days[0].isoformat() if validation_days else None,
        "validation_end": validation_days[-1].isoformat() if validation_days else None,
        "sealed_test_start": sealed_days[0].isoformat() if sealed_days else None,
        "sealed_test_end": sealed_days[-1].isoformat() if sealed_days else None,
    }


def _load_rows(path: Path, rows_remaining: int | None) -> tuple[list[dict[str, Any]], int | None]:
    if rows_remaining == 0:
        return [], 0
    limit = rows_remaining if rows_remaining is not None else None
    rows = list(iter_nifty_futures_minute_file(path, limit=limit))
    if rows_remaining is None:
        return rows, None
    return rows, max(0, rows_remaining - len(rows))


def _validate_rows(rows: list[dict[str, Any]], path: Path) -> None:
    meridian = validate_for_meridian_futures(rows)
    if not meridian["valid"]:
        raise ValueError(f"Meridian futures validation failed for {path}: {meridian['errors'][:5]}")
    real_feed = validate_real_futures_feed(rows)
    if not real_feed["validated"]:
        raise ValueError(f"Sleeve F real futures validation failed for {path}: {real_feed['failures'][:5]}")


def _sealed_test_from_summary(summary: Mapping[str, Any], split: Mapping[str, Any]) -> dict[str, Any]:
    gate_floor = float(DEFAULT_PROMOTION_THRESHOLDS.worst_fold_floor_bps)
    passed = bool(summary["trades"] > 0 and summary["net_ev_bps"] > 0.0 and summary["worst_fold_bps"] > gate_floor)
    return {
        "skipped": summary["trades"] == 0,
        "passed": passed,
        "config_id": summary["config_id"],
        "trading_days": summary["trading_days"],
        "fraction": split["sealed_test_fraction"],
        "net_ev_bps": summary["net_ev_bps"],
        "worst_fold_bps": summary["worst_fold_bps"],
        "touch_rate": summary["touch_rate"],
        "outcome_counts": summary["outcome_counts"],
        "metrics": summary["metrics"],
        "folds": summary["selection_folds"],
        "cost_scenarios": summary["cost_scenarios"],
        "stress_survives_5bps": summary["stress_survives_5bps"],
    }


def _console_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    candidate = summary["report"]["candidate"]
    return {
        "output": summary["output"],
        "rows_loaded": summary["rows_loaded"],
        "source_files_used": len(summary["source_files_used"]),
        "best_config": candidate["config_id"],
        "trades": candidate["trades"],
        "net_ev_bps": candidate["net_ev_bps"],
        "touch_rate": candidate["touch_rate"],
        "stress_survives_5bps": candidate["stress_survives_5bps"],
        "outcome_counts": candidate["outcome_counts"],
        "sealed_test_passed": candidate["sealed_test_passed"],
        "status": summary["report"]["status"],
    }


def _file_date(path: Path) -> date | None:
    match = re.search(r"nifty_fut_(\d{2})_(\d{2})_(\d{4})\.csv$", path.name)
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    return date(year, month, day)


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _minute_of_day(value: Any) -> int:
    text = str(value or "")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.hour * 60 + parsed.minute
    except ValueError:
        pass
    if len(text) >= 16 and text[11:13].isdigit() and text[14:16].isdigit():
        return int(text[11:13]) * 60 + int(text[14:16])
    return time.max.hour * 60 + time.max.minute


if __name__ == "__main__":
    main()
