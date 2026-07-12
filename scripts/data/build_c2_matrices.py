#!/usr/bin/env python3
"""Build outcome-blind Campaign 2 feature matrices from the futures archive."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# Permit the documented ``python scripts/data/build_c2_matrices.py`` invocation
# without requiring callers to set PYTHONPATH.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from features.c2_sets import (
    FEATURESET_CONFIG,
    build_c2_matrices,
    iter_c2_matrices,
    c2p_config_hash,
    c2w_config_hash,
    validate_expiry_derivation,
)


DEFAULT_ARCHIVE = Path("/Users/onkarj012/Projects/market/intranet_optinet/data/option_data/nifty_data/nifty_fut")
DEFAULT_EXPIRIES = Path("runs/sleeve-f-data-contract/expiries.csv")
DEFAULT_CONTRACT_CALENDAR = Path("runs/sleeve-f-data-contract/contract_calendar.csv")
DEFAULT_VIX = Path("runs/sleeve-f-calendar-vix/india_vix_clean.csv")


def _archive_path(root: Path, session: pd.Timestamp) -> Path:
    return root / str(session.year) / str(session.month) / f"nifty_fut_{session:%d_%m_%Y}.csv"


def archive_dates(root: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(day for day in pd.date_range(start, end, freq="D") if _archive_path(root, day).exists())


def load_archive(root: Path, dates: pd.DatetimeIndex):
    """Read minute bars only; this loader intentionally has no label inputs."""
    for session in dates:
        path = _archive_path(root, session)
        yield pd.read_csv(path, usecols=["date", "time", "open", "high", "low", "close", "oi", "volume"])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _nan_rates(frame: pd.DataFrame, feature_columns: list[str]) -> dict[str, float]:
    return {column: float(frame[column].isna().mean()) if len(frame) else 0.0 for column in feature_columns}


def build(args: argparse.Namespace) -> dict[str, object]:
    started = time.monotonic()
    trading_dates = archive_dates(args.archive_root, args.from_date, args.to_date)
    if trading_dates.empty:
        raise FileNotFoundError(f"no archive CSVs in {args.archive_root} for {args.from_date.date()}..{args.to_date.date()}")
    expiries = pd.read_csv(args.expiries)
    contract_calendar = pd.read_csv(args.contract_calendar)
    vix = pd.read_csv(args.vix)
    expiry_validation = validate_expiry_derivation(trading_dates, expiries)
    if expiry_validation["overlap_count"] and expiry_validation["match_rate"] != 1.0:
        raise ValueError(f"expiry derivation mismatch: {expiry_validation}")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    c2w_path = args.out_dir / "c2w.parquet"
    c2p_path = args.out_dir / "c2p.parquet"
    # Append one completed session at a time.  The iterator retains only
    # bounded trailing-60 state, preserving causality without materializing a
    # prohibitively wide multi-year incumbent feature frame.
    c2w_writer: pq.ParquetWriter | None = None
    c2p_writer: pq.ParquetWriter | None = None
    c2w_rows = c2p_rows = 0
    c2w_nan_counts = {column: 0 for column in FEATURESET_CONFIG["c2w"]["columns"]}
    c2p_nan_counts = {column: 0 for column in FEATURESET_CONFIG["c2p"]["columns"]}
    c2w_columns: list[str] = []
    c2p_columns: list[str] = []
    try:
        for c2w, c2p in iter_c2_matrices(
            load_archive(args.archive_root, trading_dates), known_expiries=expiries, contract_calendar=contract_calendar,
            vix=vix, trading_dates=trading_dates,
        ):
            c2w_columns, c2p_columns = list(c2w.columns), list(c2p.columns)
            for column in c2w_nan_counts:
                c2w_nan_counts[column] += int(c2w[column].isna().sum())
            for column in c2p_nan_counts:
                c2p_nan_counts[column] += int(c2p[column].isna().sum())
            c2w_rows += len(c2w)
            c2p_rows += len(c2p)
            c2w_table, c2p_table = pa.Table.from_pandas(c2w, preserve_index=False), pa.Table.from_pandas(c2p, preserve_index=False)
            if c2w_writer is None:
                c2w_writer = pq.ParquetWriter(c2w_path, c2w_table.schema, compression="zstd")
                c2p_writer = pq.ParquetWriter(c2p_path, c2p_table.schema, compression="zstd")
            c2w_writer.write_table(c2w_table)
            c2p_writer.write_table(c2p_table)
    finally:
        if c2w_writer is not None:
            c2w_writer.close()
        if c2p_writer is not None:
            c2p_writer.close()
    elapsed = time.monotonic() - started
    report: dict[str, object] = {
        "date_range": {"from": str(args.from_date.date()), "to": str(args.to_date.date())},
        "expiry_derivation_validation": expiry_validation,
        "c2w": {
            "sha256": _sha256(c2w_path),
            "row_count": c2w_rows,
            "columns": c2w_columns,
            "config_hash": c2w_config_hash(),
            "nan_rates": {column: (count / c2w_rows if c2w_rows else 0.0) for column, count in c2w_nan_counts.items()},
        },
        "c2p": {
            "sha256": _sha256(c2p_path),
            "row_count": c2p_rows,
            "columns": c2p_columns,
            "config_hash": c2p_config_hash(),
            "nan_rates": {column: (count / c2p_rows if c2p_rows else 0.0) for column, count in c2p_nan_counts.items()},
        },
    }
    (args.out_dir / "hashes.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    run_report = {
        "build_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_seconds": elapsed,
    }
    (args.out_dir / "run_report.json").write_text(json.dumps(run_report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {**report, **run_report}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--from-date", type=pd.Timestamp, default=pd.Timestamp("2020-01-01"))
    parser.add_argument("--to-date", type=pd.Timestamp, default=pd.Timestamp("2026-06-30"))
    parser.add_argument("--out-dir", type=Path, default=Path("runs/sleeve-f-c2-matrices"))
    parser.add_argument("--expiries", type=Path, default=DEFAULT_EXPIRIES)
    parser.add_argument("--contract-calendar", type=Path, default=DEFAULT_CONTRACT_CALENDAR)
    parser.add_argument("--vix", type=Path, default=DEFAULT_VIX)
    return parser.parse_args()


if __name__ == "__main__":
    result = build(parse_args())
    print(json.dumps({
        "runtime_seconds": result["runtime_seconds"],
        "c2w_rows": result["c2w"]["row_count"],
        "c2p_rows": result["c2p"]["row_count"],
        "expiry_match_rate": result["expiry_derivation_validation"]["match_rate"],
    }, sort_keys=True))
