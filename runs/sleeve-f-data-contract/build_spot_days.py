#!/usr/bin/env python3
"""Fill missing NIFTY spot per-day files from the offline minute archive."""
from __future__ import annotations

import argparse
import json
import re
from datetime import date
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path("/Users/onkarj012/Projects/market/meridian")
ARCHIVE_ROOT = Path("/Users/onkarj012/Projects/market/intranet_optinet/data/option_data/nifty_data/nifty_spot")
SOURCE = Path("/Users/onkarj012/Projects/market/intranet_optinet/data/nifty_intraday/NIFTY 50_minute.csv")
REPORT = PROJECT_ROOT / "runs/sleeve-f-data-contract/spot_backfill_report.json"
START = pd.Timestamp("2024-11-01")
END = pd.Timestamp("2026-06-16")
CHECK_START = pd.Timestamp("2024-10-01")
SESSION_START = "09:15:00"
SESSION_END = "15:30:00"
FILE_RE = re.compile(r"nifty_spot(?P<day>\d{2})_(?P<month>\d{2})_(?P<year>\d{4})\.csv$")
OUTPUT_COLUMNS = ["date", "time", "symbol", "open", "high", "low", "close"]


def spot_path(day: pd.Timestamp | date, root: Path = ARCHIVE_ROOT) -> Path:
    day = pd.Timestamp(day)
    return root / str(day.year) / str(day.month) / f"nifty_spot{day:%d_%m_%Y}.csv"


def existing_dates(root: Path) -> dict[pd.Timestamp, Path]:
    result: dict[pd.Timestamp, Path] = {}
    for path in root.glob("*/*/nifty_spot*.csv"):
        match = FILE_RE.fullmatch(path.name)
        if not match:
            continue
        day = pd.Timestamp(
            year=int(match.group("year")), month=int(match.group("month")), day=int(match.group("day"))
        ).normalize()
        result[day] = path
    return result


def load_source(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, usecols=["date", "open", "high", "low", "close"])
    frame["timestamp"] = pd.to_datetime(frame["date"], errors="coerce")
    if frame["timestamp"].isna().any():
        raise ValueError("source contains unparseable timestamps")
    frame = frame[frame["timestamp"].dt.normalize().between(CHECK_START, END)].copy()
    frame["time"] = frame["timestamp"].dt.strftime("%H:%M:%S")
    frame = frame[frame["time"].between(SESSION_START, SESSION_END)].copy()
    for column in ["open", "high", "low", "close"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").round(2)
    if frame[["open", "high", "low", "close"]].isna().any().any():
        raise ValueError("source contains non-numeric OHLC values in the target range")
    frame["trade_date"] = frame["timestamp"].dt.normalize()
    return frame.sort_values("timestamp").reset_index(drop=True)


def output_frame(day: pd.Timestamp, source: pd.DataFrame) -> pd.DataFrame:
    rows = source[source["trade_date"].eq(day)].copy()
    return pd.DataFrame(
        {
            "date": rows["trade_date"].dt.strftime("%Y-%m-%d"),
            "time": rows["time"],
            "symbol": "NIFTY",
            "open": rows["open"].round(2),
            "high": rows["high"].round(2),
            "low": rows["low"].round(2),
            "close": rows["close"].round(2),
        }
    )[OUTPUT_COLUMNS].reset_index(drop=True)


def compare_day(day: pd.Timestamp, derived: pd.DataFrame, path: Path) -> dict[str, object]:
    vintage = pd.read_csv(path)
    required = set(OUTPUT_COLUMNS)
    missing = required - set(vintage.columns)
    if missing:
        raise ValueError(f"{path} missing columns: {sorted(missing)}")
    vintage = vintage[OUTPUT_COLUMNS].copy()
    merged = derived[["time", "close"]].merge(
        vintage[["time", "close"]], on="time", how="outer", suffixes=("_derived", "_vintage"), indicator=True
    )
    close_diff = (merged["close_derived"] - merged["close_vintage"]).abs()
    denominator = merged["close_vintage"].abs().where(merged["close_vintage"].abs().ne(0))
    relative = close_diff / denominator
    max_abs = float(close_diff.max()) if not close_diff.empty else 0.0
    max_relative = float(relative.max()) if relative.notna().any() else 0.0
    return {
        "trade_date": day.strftime("%Y-%m-%d"),
        "path": str(path),
        "derived_rows": len(derived),
        "vintage_rows": len(vintage),
        "matching_times": int(merged["_merge"].eq("both").sum()),
        "missing_or_extra_times": int(merged["_merge"].ne("both").sum()),
        "max_abs_close_diff": max_abs,
        "max_relative_close_diff": max_relative,
        "drift_over_threshold": bool(max_relative > 0.0005 or merged["_merge"].ne("both").any()),
    }


def select_overlap_days(available: set[pd.Timestamp], existing: dict[pd.Timestamp, Path]) -> list[pd.Timestamp]:
    october = sorted(day for day in available & set(existing) if day.year == 2024 and day.month == 10)[:3]
    twenty_six = sorted(day for day in available & set(existing) if day.year == 2026)[:3]
    if len(october) < 3 or len(twenty_six) < 3:
        raise RuntimeError(f"need 3 overlap days in 2024-10 and 2026; got {len(october)} and {len(twenty_six)}")
    return october + twenty_six


def count_archive(root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for day in existing_dates(root):
        counts[str(day.year)] = counts.get(str(day.year), 0) + 1
    return dict(sorted(counts.items()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=str(SOURCE))
    parser.add_argument("--archive-root", default=str(ARCHIVE_ROOT))
    parser.add_argument("--report", default=str(REPORT))
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.archive_root)
    source = load_source(Path(args.source))
    existing = existing_dates(root)
    available = set(source.loc[source["trade_date"].between(START, END), "trade_date"].unique())
    overlap_available = set(source["trade_date"].unique())
    overlaps = select_overlap_days(overlap_available, existing)
    overlap_reports = [compare_day(day, output_frame(day, source), existing[day]) for day in overlaps]
    blocked = any(report["drift_over_threshold"] for report in overlap_reports)
    report: dict[str, object] = {
        "source": str(args.source),
        "archive_root": str(root),
        "range": {"start": START.strftime("%Y-%m-%d"), "end": END.strftime("%Y-%m-%d")},
        "overlap_checks": overlap_reports,
        "blocked": blocked,
        "written": [],
        "skipped": [],
        "missing_source_days": [],
    }
    if blocked:
        report["blocked_reason"] = "Vintage overlap close drift exceeded 0.05% or minute times differed; no files written."
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2))
        return 2

    for day in sorted(available):
        path = spot_path(day, root)
        if path.exists() and not args.overwrite:
            report["skipped"].append(day.strftime("%Y-%m-%d"))
            continue
        frame = output_frame(day, source)
        if frame.empty:
            report["missing_source_days"].append(day.strftime("%Y-%m-%d"))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, index=False)
        report["written"].append({"trade_date": day.strftime("%Y-%m-%d"), "path": str(path), "rows": len(frame)})

    report["file_counts_by_year"] = count_archive(root)
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"overlap_checks={len(overlap_reports)} blocked={blocked}")
    print(f"written={len(report['written'])} skipped={len(report['skipped'])}")
    print(f"file_counts_by_year={report['file_counts_by_year']}")
    for item in overlap_reports:
        print(
            f"overlap {item['trade_date']}: max_abs_close_diff={item['max_abs_close_diff']:.6f} "
            f"max_relative_close_diff={item['max_relative_close_diff']:.8%}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
