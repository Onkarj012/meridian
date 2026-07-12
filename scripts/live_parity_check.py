"""Run engine-specific offline adapters against captured live snapshots."""
from __future__ import annotations

import argparse
from importlib import import_module
from pathlib import Path
import sys
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from contracts.live_snapshot import read_live_snapshots
from evidence.live_parity import ParityReport, ParityReportWriter, compare, recompute_from_snapshot


def run_parity(
    snapshot_path: str | Path,
    out_dir: str | Path,
    feature_fn: Callable[..., Any],
    score_fn: Callable[..., Any],
) -> list[ParityReport]:
    """Recompute every verified JSONL snapshot and append reports to ``out_dir``."""
    destination = Path(out_dir)
    reports = [
        compare(snapshot, recompute_from_snapshot(snapshot, feature_fn=feature_fn, score_fn=score_fn))
        for snapshot in read_live_snapshots(snapshot_path)
    ]
    writer = ParityReportWriter(destination / "live_parity_reports.jsonl")
    for report in reports:
        writer.append(report)
        (destination / f"{report.snapshot_id}.live_parity.md").write_text(report.to_markdown(), encoding="utf-8")
    return reports


def _load_callable(reference: str) -> Callable[..., Any]:
    module_name, separator, attribute = reference.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("callable references must use module:attribute")
    value = getattr(import_module(module_name), attribute)
    if not callable(value):
        raise TypeError(f"{reference} is not callable")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot_path", help="verified live-snapshot JSONL path")
    parser.add_argument("out_dir", help="directory for JSONL and Markdown reports")
    parser.add_argument("--feature-fn", required=True, help="offline adapter as module:attribute")
    parser.add_argument("--score-fn", required=True, help="offline adapter as module:attribute")
    args = parser.parse_args()
    reports = run_parity(args.snapshot_path, args.out_dir, _load_callable(args.feature_fn), _load_callable(args.score_fn))
    if not reports:
        print("error: no live snapshots were read; parity cannot be considered successful", file=sys.stderr)
        return 1
    for report in reports:
        print(f"{report.snapshot_id}: {report.verdict}")
    return 0 if all(report.verdict != "FAIL" for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
