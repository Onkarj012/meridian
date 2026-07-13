#!/usr/bin/env python3
"""Build the deterministic Campaign 1 feature/label matrix."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from features.c1_matrix import archive_dates, artifact_report, build_c1_matrix, load_archive_sessions


DEFAULT_ARCHIVE = os.environ.get("SLEEVE_F_FUT_ARCHIVE")
DEFAULT_DATA_ROOT = os.environ.get("SLEEVE_F_DATA_DIR") or os.environ.get("SLEEVE_F_MINUTE_ROOT")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build(args: argparse.Namespace) -> dict[str, object]:
    dates = archive_dates(args.archive_root, args.from_date, args.to_date)
    if dates.empty:
        raise FileNotFoundError(f"no archive CSVs in {args.archive_root} for {args.from_date.date()}..{args.to_date.date()}")
    matrix = build_c1_matrix(
        load_archive_sessions(args.archive_root, dates), data_root=args.data_root,
        spot_source=args.spot_source, spot_index_path=args.spot_index_path,
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    parquet_path = args.out_dir / "c1.parquet"
    table = pa.Table.from_pandas(matrix, preserve_index=False)
    pq.write_table(table, parquet_path, compression="zstd", use_dictionary=False, write_statistics=True)
    report = artifact_report(matrix, _sha256(parquet_path))
    report["date_range"] = {"from": str(args.from_date.date()), "to": str(args.to_date.date())}
    report["session_count"] = int(len(dates))
    canonical = json.dumps(report, indent=2, sort_keys=True) + "\n"
    (args.out_dir / "hashes.json").write_text(canonical, encoding="utf-8")
    (args.out_dir / "run_report.json").write_text(canonical, encoding="utf-8")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", type=Path, default=Path(DEFAULT_ARCHIVE) if DEFAULT_ARCHIVE else None)
    parser.add_argument("--data-root", type=Path, default=Path(DEFAULT_DATA_ROOT) if DEFAULT_DATA_ROOT else None)
    parser.add_argument("--spot-source", choices=("auto", "spot_dir", "spot_index_file"), default="auto")
    parser.add_argument("--spot-index-path", type=Path)
    parser.add_argument("--from-date", type=pd.Timestamp, default=pd.Timestamp("2020-01-01"))
    parser.add_argument("--to-date", type=pd.Timestamp, default=pd.Timestamp("2026-06-30"))
    parser.add_argument("--out-dir", type=Path, default=Path("runs/sleeve-f-c1-matrix"))
    args = parser.parse_args()
    if args.archive_root is None:
        parser.error("--archive-root or SLEEVE_F_FUT_ARCHIVE is required")
    if args.data_root is None:
        parser.error("--data-root, SLEEVE_F_DATA_DIR, or SLEEVE_F_MINUTE_ROOT is required")
    return args


if __name__ == "__main__":
    print(json.dumps(build(parse_args()), sort_keys=True))
