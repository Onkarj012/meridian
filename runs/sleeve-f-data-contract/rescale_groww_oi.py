#!/usr/bin/env python3
"""Normalize Groww's ×100-underreported OI in the backfilled archive.

The target set is deliberately the explicit Groww gap-day list, not every CSV
under the archive root.  This prevents vintage, Kite, and other provider files
from being changed.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ingest.expired_common import minute_file_path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARCHIVE = Path("/Users/onkarj012/Projects/market/intranet_optinet/data/option_data/nifty_data/nifty_fut")
DEFAULT_CALENDAR = ROOT / "runs/sleeve-f-data-contract/contract_calendar.csv"
DEFAULT_GAPS = ROOT / "runs/sleeve-f-data-contract/gap_days.csv"
GROWW_OI_X100_START_DATE = "2025-01-01"
NORMALIZED_RATIO_MIN = 0.20
NORMALIZED_RATIO_MAX = 2.0
X100_RATIO_MIN = 10.0


def last_nonzero_oi(frame: pd.DataFrame) -> float | None:
    oi = pd.to_numeric(frame["oi"], errors="coerce")
    nonzero = oi[oi.ne(0) & oi.notna()]
    return float(nonzero.iloc[-1]) if not nonzero.empty else None


def oi_ratio(calendar_row: pd.Series, frame: pd.DataFrame) -> float | None:
    expected = pd.to_numeric(pd.Series([calendar_row["front_oi"]]), errors="coerce").iloc[0]
    observed = last_nonzero_oi(frame)
    if pd.isna(expected) or observed in (None, 0):
        return None
    return float(expected) / observed


def classify_day(calendar_row: pd.Series, frame: pd.DataFrame) -> str:
    """Classify a file by bhavcopy/file OI ratio, with an idempotent no-op."""
    ratio = oi_ratio(calendar_row, frame)
    if ratio is None:
        return "no_observation"
    if NORMALIZED_RATIO_MIN <= ratio <= NORMALIZED_RATIO_MAX:
        return "already_normalized"
    if ratio >= X100_RATIO_MIN:
        return "x100"
    raise ValueError(
        f"ambiguous OI ratio {ratio:.6f} for {calendar_row['trade_date']}; "
        f"expected {NORMALIZED_RATIO_MIN}..{NORMALIZED_RATIO_MAX} or >= {X100_RATIO_MIN}"
    )


def rescale_file(path: Path, calendar_row: pd.Series) -> str:
    frame = pd.read_csv(path)
    classification = classify_day(calendar_row, frame)
    if classification != "x100":
        return classification
    frame["oi"] = pd.to_numeric(frame["oi"], errors="coerce") * 100
    # Atomic replacement avoids leaving a partially written day file behind.
    with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        frame.to_csv(handle, index=False)
    os.replace(temporary, path)
    return "rescaled"


def run(archive: Path, calendar_path: Path, gaps_path: Path) -> dict[str, int]:
    calendar = pd.read_csv(calendar_path, dtype=str).set_index("trade_date")
    gaps = pd.read_csv(gaps_path, dtype=str)
    counts = {"rescaled": 0, "already_normalized": 0, "skipped": 0, "missing": 0, "ambiguous": 0}
    for trade_date in gaps["trade_date"]:
        if trade_date == "2024-11-01":
            counts["skipped"] += 1
            continue
        if trade_date < GROWW_OI_X100_START_DATE:
            counts["skipped"] += 1
            continue
        path = minute_file_path(trade_date, archive)
        if not path.exists():
            counts["missing"] += 1
            continue
        try:
            status = rescale_file(path, calendar.loc[trade_date])
        except ValueError as exc:
            counts["ambiguous"] += 1
            print(f"ambiguous {trade_date}: {exc}")
            continue
        if status == "rescaled":
            counts["rescaled"] += 1
            print(f"rescaled {trade_date}")
        elif status == "already_normalized":
            counts[status] += 1
        else:
            counts["skipped"] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--calendar", type=Path, default=DEFAULT_CALENDAR)
    parser.add_argument("--gap-days", type=Path, default=DEFAULT_GAPS)
    args = parser.parse_args()
    counts = run(args.archive, args.calendar, args.gap_days)
    print("summary: " + " ".join(f"{key}={value}" for key, value in counts.items()))
    return 1 if counts["missing"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
