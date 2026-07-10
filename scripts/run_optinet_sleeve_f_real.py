"""Run Sleeve F real-feed evidence on a bounded OptiNet NIFTY futures sample."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evidence.sleeve_f_real import DEFAULT_FUTURES_ROUND_TRIP_COST_BPS, SLEEVE_F_HOLDOUT_TOUCH_COUNT, run_sleeve_f_real_feed_evidence
from evidence.stats import SLEEVE_F_CAMPAIGN_TRIALS
from features.sleeve_f import validate_real_futures_feed
from ingest.optinet_data import (
    build_optinet_source_manifest,
    iter_nifty_futures_minute_file,
    summarize_minute_quality,
    validate_for_meridian_futures,
)

DEFAULT_DATA_ROOT = Path("/Users/onkarj012/Projects/market/intranet_optinet/data")
DEFAULT_OUTPUT = Path("runs/optinet-sleeve-f-real-sample/summary.json")
DEFAULT_LIMIT_ROWS = 2000
DEFAULT_TARGET_BPS = 50.0
DEFAULT_STOP_BPS = 50.0
DEFAULT_HORIZON_BARS = 5
SAMPLE_LIMITATION = "sample replay only; not promoted; no live trading"


def run(
    *,
    data_root: str | Path = DEFAULT_DATA_ROOT,
    limit_rows: int = DEFAULT_LIMIT_ROWS,
    output: str | Path = DEFAULT_OUTPUT,
    grid: Iterable[dict[str, Any]] | None = None,
    grid_path: str | Path | None = None,
    target_bps: float = DEFAULT_TARGET_BPS,
    stop_bps: float = DEFAULT_STOP_BPS,
    horizon_bars: int = DEFAULT_HORIZON_BARS,
    fixture_mode: bool = False,
    sample_mode: bool = True,
    cost_bps: float = DEFAULT_FUTURES_ROUND_TRIP_COST_BPS,
    allow_overlap: bool = False,
) -> dict[str, Any]:
    """Load a bounded OptiNet futures sample, run evidence, and write summary JSON."""
    root = Path(data_root)
    row_limit = int(limit_rows)
    if row_limit <= 0:
        raise ValueError("limit_rows must be positive")

    manifest = build_optinet_source_manifest(root)
    source_files = _discover_nifty_futures_files(root)
    rows, used_files = _load_rows(source_files, row_limit)
    if not rows:
        raise ValueError(f"no normalized NIFTY futures rows loaded from {root}")

    meridian_validation = validate_for_meridian_futures(rows)
    real_feed_validation = validate_real_futures_feed(rows)
    if not meridian_validation["valid"]:
        raise ValueError(f"Meridian futures validation failed: {meridian_validation['errors'][:5]}")
    if not real_feed_validation["validated"]:
        raise ValueError(f"Sleeve F real futures validation failed: {real_feed_validation['failures'][:5]}")

    grid_configs = _load_grid(Path(grid_path)) if grid_path is not None else (list(grid) if grid is not None else [_grid_config(target_bps, stop_bps, horizon_bars)])
    grid_committed = _grid_is_git_tracked_and_clean(Path(grid_path)) if grid_path is not None else False
    effective_fixture_mode = bool(fixture_mode) if sample_mode else False
    if not sample_mode:
        _require_signal_configs(grid_configs)
    report = run_sleeve_f_real_feed_evidence(
        rows,
        grid=grid_configs,
        grid_committed=grid_committed,
        fixture_mode=effective_fixture_mode or sample_mode,
        cost_bps=cost_bps,
        allow_overlap=allow_overlap,
    )
    if effective_fixture_mode or sample_mode:
        report["promoted"] = False
        report["status"] = "quarantined"
        report["reason"] = report.get("reason") or "sample_replay_only_not_promoted"
        report.setdefault("prerequisites", {})["sample_mode"] = bool(sample_mode)

    output_path = Path(output)
    summary = {
        "data_root": str(root),
        "output": str(output_path),
        "source_files_used": [str(path) for path in used_files],
        "rows_loaded": len(rows),
        "limit_rows": row_limit,
        "normalization_quality": {
            "minute_quality": summarize_minute_quality(rows),
            "meridian_futures_validation": meridian_validation,
            "real_futures_validation": real_feed_validation,
        },
        "source_manifest": _compact_manifest(manifest),
        "grid": grid_configs,
        "grid_path": str(grid_path) if grid_path is not None else None,
        "committed_grid": grid_committed,
        "dsr_trials": SLEEVE_F_CAMPAIGN_TRIALS,
        "holdout_touch_count": SLEEVE_F_HOLDOUT_TOUCH_COUNT,
        "cost_bps": float(cost_bps),
        "allow_overlap": bool(allow_overlap),
        "fixture_mode": bool(effective_fixture_mode),
        "sample_mode": bool(sample_mode),
        "limitation": SAMPLE_LIMITATION,
        "report": report,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def main(argv: Sequence[str] | None = None) -> dict[str, Any]:
    args = _parser().parse_args(argv)
    grid = None if args.grid_file else _parse_grid(args.grid_json, args.grid, args.target_bps, args.stop_bps, args.horizon_bars)
    summary = run(
        data_root=args.data_root,
        limit_rows=args.limit_rows,
        output=args.output,
        grid=grid,
        grid_path=args.grid_file,
        fixture_mode=args.fixture_mode,
        sample_mode=args.sample_mode,
        cost_bps=args.cost_bps,
        allow_overlap=args.allow_overlap,
    )
    print(json.dumps(_console_summary(summary), sort_keys=True))
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    parser.add_argument("--limit-rows", type=int, default=DEFAULT_LIMIT_ROWS)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--target-bps", type=float, default=DEFAULT_TARGET_BPS)
    parser.add_argument("--stop-bps", type=float, default=DEFAULT_STOP_BPS)
    parser.add_argument("--horizon-bars", type=int, default=DEFAULT_HORIZON_BARS)
    parser.add_argument("--cost-bps", type=float, default=DEFAULT_FUTURES_ROUND_TRIP_COST_BPS)
    parser.add_argument("--allow-overlap", action="store_true", default=False)
    parser.add_argument(
        "--grid",
        action="append",
        default=[],
        help="Repeatable grid item as target_bps,stop_bps,horizon_bars[,min_realized_vol].",
    )
    parser.add_argument("--grid-json", help="JSON list of grid config objects.")
    parser.add_argument("--grid-file", help="JSON grid file; must be tracked and clean for committed_grid=true.")
    parser.add_argument("--fixture-mode", dest="fixture_mode", action="store_true", default=False)
    parser.add_argument("--no-fixture-mode", dest="fixture_mode", action="store_false")
    parser.add_argument("--sample-mode", dest="sample_mode", action="store_true", default=True)
    parser.add_argument("--no-sample-mode", dest="sample_mode", action="store_false")
    return parser


def _discover_nifty_futures_files(data_root: Path) -> list[Path]:
    root = data_root / "option_data" / "nifty_data" / "nifty_fut"
    if not root.exists():
        raise FileNotFoundError(f"NIFTY futures directory not found: {root}")
    files = [path for path in root.rglob("*.csv") if path.is_file()]
    return sorted(files, key=_file_sort_key, reverse=True)


def _file_sort_key(path: Path) -> tuple[int, int, int, str]:
    match = re.search(r"nifty_fut_(\d{2})_(\d{2})_(\d{4})\.csv$", path.name)
    if match:
        day, month, year = (int(part) for part in match.groups())
        return year, month, day, str(path)
    return 0, 0, 0, str(path)


def _load_rows(source_files: list[Path], limit_rows: int) -> tuple[list[dict[str, Any]], list[Path]]:
    rows: list[dict[str, Any]] = []
    used_files: list[Path] = []
    for path in source_files:
        remaining = limit_rows - len(rows)
        if remaining <= 0:
            break
        loaded = list(iter_nifty_futures_minute_file(path, limit=remaining))
        if loaded:
            rows.extend(loaded)
            used_files.append(path)
    return rows, used_files


def _parse_grid(
    grid_json: str | None,
    grid_items: Sequence[str],
    target_bps: float,
    stop_bps: float,
    horizon_bars: int,
) -> list[dict[str, Any]]:
    if grid_json:
        parsed = json.loads(grid_json)
        if not isinstance(parsed, list):
            raise ValueError("--grid-json must be a JSON list")
        return [dict(item) for item in parsed]
    if grid_items:
        return [_parse_grid_item(item, index) for index, item in enumerate(grid_items)]
    return [_grid_config(target_bps, stop_bps, horizon_bars)]


def _require_signal_configs(grid: Iterable[Mapping[str, Any]]) -> None:
    for config in grid:
        if not dict(config.get("signal_config") or {}):
            raise ValueError("signal_config required for non-fixture Sleeve F replay")


def _load_grid(path: Path) -> list[dict[str, Any]]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, list):
        raise ValueError("grid file must contain a JSON list")
    return [dict(item) for item in parsed]


def _parse_grid_item(item: str, index: int) -> dict[str, Any]:
    values = [part.strip() for part in item.split(",")]
    if len(values) not in (3, 4):
        raise ValueError("--grid items must have 3 or 4 comma-separated values")
    config = _grid_config(float(values[0]), float(values[1]), int(values[2]), config_id=f"cli_grid_{index}")
    if len(values) == 4:
        config["min_realized_vol"] = float(values[3])
    return config


def _grid_config(
    target_bps: float,
    stop_bps: float,
    horizon_bars: int,
    *,
    config_id: str = "cli_grid_0",
) -> dict[str, Any]:
    return {
        "id": config_id,
        "target_bps": float(target_bps),
        "stop_bps": float(stop_bps),
        "horizon_bars": int(horizon_bars),
    }


def _compact_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    families = manifest.get("families", {})
    return {
        "manifest_id": manifest.get("manifest_id"),
        "generated_at": manifest.get("generated_at"),
        "selected_families": manifest.get("selected_families", []),
        "total_bytes": manifest.get("total_bytes", 0),
        "families": {
            name: {
                "exists": summary.get("exists"),
                "file_count": summary.get("file_count", 0),
                "total_bytes": summary.get("total_bytes", 0),
            }
            for name, summary in families.items()
        },
    }


def _grid_is_git_tracked_and_clean(path: Path) -> bool:
    """Return whether ``path`` is tracked and unchanged from HEAD."""
    try:
        resolved = path.resolve()
        relative = resolved.relative_to(ROOT)
    except (OSError, ValueError):
        return False
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", str(relative)],
            cwd=ROOT,
            check=False,
            capture_output=True,
        )
        if tracked.returncode != 0:
            return False
        clean = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", str(relative)],
            cwd=ROOT,
            check=False,
            capture_output=True,
        )
    except OSError:
        return False
    return clean.returncode == 0


def _console_summary(summary: dict[str, Any]) -> dict[str, Any]:
    candidate = summary.get("report", {}).get("candidate") or {}
    metrics = summary.get("report", {}).get("metrics") or candidate.get("metrics") or {}
    return {
        "output": str(summary.get("output", DEFAULT_OUTPUT)),
        "rows_loaded": summary["rows_loaded"],
        "source_files_used": len(summary["source_files_used"]),
        "status": summary["report"].get("status"),
        "promoted": summary["report"].get("promoted"),
        "trades": metrics.get("trades", candidate.get("trades", 0)),
        "net_ev_bps": metrics.get("net_ev_bps", candidate.get("net_ev_bps", 0.0)),
        "stress_survives_5bps": candidate.get("stress_survives_5bps", False),
        "limitation": summary["limitation"],
    }


if __name__ == "__main__":
    main()
