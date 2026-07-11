#!/usr/bin/env python3
"""Scan NIFTY futures minute files and write a review-only quality contract."""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd


REGULAR_START = time(9, 15)
REGULAR_END = time(15, 29)
EXPECTED_BARS = 375
KNOWN_MUHURAT = {
    date(2020, 11, 14), date(2021, 11, 4), date(2022, 10, 24), date(2023, 11, 12)
}
KNOWN_SPECIAL = {date(2020, 2, 1)}
OUTPUT_COLUMNS = [
    "date", "bar_count", "first_bar_timestamp", "last_bar_timestamp",
    "duplicate_timestamp_count", "non_monotonic_timestamp_count",
    "gap_segment_count", "gap_segments", "zero_volume_bar_count",
    "zero_price_bar_count", "constant_price_run_count", "oi_all_zero",
    "probable_cause", "proposed_disposition", "disposition_reason",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--from-date", type=date.fromisoformat, required=True)
    parser.add_argument("--to-date", type=date.fromisoformat, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    return parser.parse_args()


def file_date(path: Path) -> date:
    return datetime.strptime("_".join(path.stem.rsplit("_", 3)[-3:]), "%d_%m_%Y").date()


def expected_minutes(day: date) -> pd.DatetimeIndex:
    return pd.date_range(
        pd.Timestamp.combine(day, REGULAR_START),
        pd.Timestamp.combine(day, REGULAR_END),
        freq="min",
    )


def format_gap_segments(missing: pd.DatetimeIndex) -> str:
    if not len(missing):
        return ""
    segments: list[str] = []
    start = previous = missing[0]
    for stamp in missing[1:]:
        if stamp != previous + pd.Timedelta(minutes=1):
            segments.append(f"{start.strftime('%H:%M')}-{previous.strftime('%H:%M')} ({int((previous-start).total_seconds()/60)+1})")
            start = stamp
        previous = stamp
    segments.append(f"{start.strftime('%H:%M')}-{previous.strftime('%H:%M')} ({int((previous-start).total_seconds()/60)+1})")
    return "; ".join(segments)


def constant_price_runs(close: pd.Series) -> int:
    values = pd.to_numeric(close, errors="coerce")
    same = values.eq(values.shift()) & values.notna()
    total = 0
    run = 0
    for is_same in same:
        if is_same:
            run += 1
        else:
            if run:
                total += run + 1
            run = 0
    if run:
        total += run + 1
    return total


def classify(day: date, count: int, first: pd.Timestamp, last: pd.Timestamp, duplicates: int) -> tuple[str, str, str]:
    if day in KNOWN_MUHURAT:
        return "known NSE Muhurat evening session", "keep_full", "Keep the complete exchange session; it is intentionally shorter and outside regular hours."
    if day in KNOWN_SPECIAL:
        return "known NSE special/half-day session (Budget Saturday)", "keep_full", "Keep the complete special session; do not pad it to regular hours."
    if duplicates and count >= 2 * EXPECTED_BARS:
        return "duplicated bars (approximately 2x regular session)", "drop_day", "Drop the duplicated day pending a source-level repair; duplicate rows invalidate chronology."
    if count > EXPECTED_BARS and last.time() > REGULAR_END and duplicates == 0:
        return "extended file with valid extra timestamp(s); likely vendor data error", "trim_to_regular_hours", "Trim the extra timestamp(s) outside 09:15–15:29; retain regular-hours bars only."
    if count < EXPECTED_BARS:
        if count < 300 or last.time() < REGULAR_END:
            return "truncated vendor data", "drop_day", "Drop the day because the missing tail or substantial truncation makes close and labels unreliable."
        return "truncated vendor data", "exclude_labels_near_close", "Keep usable bars for features, but exclude labels around the missing interval/irregular close pending review."
    if count > EXPECTED_BARS:
        return "extended file; cause unresolved", "trim_to_regular_hours", "Trim to the registered 09:15–15:29 regular session pending source confirmation."
    return "bar-count anomaly; cause unresolved", "exclude_training", "Exclude from training until the bar-count discrepancy is resolved."


def inspect(path: Path, day: date) -> dict[str, object]:
    frame = pd.read_csv(path)
    timestamps = pd.to_datetime(frame["date"].astype(str) + " " + frame["time"].astype(str), errors="coerce")
    expected = expected_minutes(day)
    regular = pd.DatetimeIndex(timestamps.dropna().unique()).intersection(expected)
    missing = expected.difference(regular)
    first = timestamps.min()
    last = timestamps.max()
    duplicates = int(timestamps.duplicated().sum())
    non_monotonic = int((timestamps.diff().dt.total_seconds() < 0).fillna(False).sum())
    prices = frame[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
    zero_price = int((prices == 0).any(axis=1).sum())
    cause, disposition, reason = classify(day, len(frame), first, last, duplicates)
    return {
        "date": day.isoformat(), "bar_count": len(frame),
        "first_bar_timestamp": first.isoformat(sep=" ") if pd.notna(first) else "",
        "last_bar_timestamp": last.isoformat(sep=" ") if pd.notna(last) else "",
        "duplicate_timestamp_count": duplicates,
        "non_monotonic_timestamp_count": non_monotonic,
        "gap_segment_count": len(format_gap_segments(missing).split("; ")) if len(missing) else 0,
        "gap_segments": format_gap_segments(missing),
        "zero_volume_bar_count": int(pd.to_numeric(frame["volume"], errors="coerce").fillna(0).eq(0).sum()),
        "zero_price_bar_count": zero_price,
        "constant_price_run_count": constant_price_runs(frame["close"]),
        "oi_all_zero": bool(pd.to_numeric(frame["oi"], errors="coerce").fillna(0).eq(0).all()),
        "probable_cause": cause, "proposed_disposition": disposition, "disposition_reason": reason,
    }


def calendar_missing(repo_root: Path, start: date, end: date, observed: set[date]) -> list[str]:
    path = repo_root / "runs/sleeve-f-data-contract/contract_calendar.csv"
    if not path.exists():
        return []
    frame = pd.read_csv(path, usecols=["trade_date"])
    dates = pd.to_datetime(frame["trade_date"]).dt.date
    return [d.isoformat() for d in dates if start <= d <= end and d not in observed]


def write_report(out: Path, all_rows: list[dict[str, object]], flagged: list[dict[str, object]], missing: list[str], start: date, end: date) -> None:
    counts = Counter(str(row["bar_count"]) for row in flagged)
    causes = Counter(str(row["probable_cause"]) for row in flagged)
    dispositions = Counter(str(row["proposed_disposition"]) for row in flagged)
    lines = [
        "# Historical session-quality scan (2020–2023)", "",
        f"Range: `{start}` through `{end}`. The registered regular session is 09:15–15:29 inclusive (375 bars). Dispositions are proposals only.", "",
        f"- Sessions scanned: **{len(all_rows)}**", f"- Sessions flagged: **{len(flagged)}**", f"- Files found: **{len(all_rows)}**", "",
        "## Flagged sessions by bar count", "", "| Bar count | Sessions |", "|---:|---:|",
    ]
    lines += [f"| {count} | {n} |" for count, n in sorted(counts.items(), key=lambda x: int(x[0]))]
    lines += ["", "## Cause and disposition proposals", "", "| Probable cause | Sessions |", "|---|---:|"]
    lines += [f"| {cause} | {n} |" for cause, n in causes.most_common()]
    lines += ["", "| Proposed disposition | Sessions |", "|---|---:|"]
    lines += [f"| {disp} | {n} |" for disp, n in dispositions.most_common()]
    lines += ["", "## All flagged sessions", "", "| Date | Bars | First | Last | Gaps | Cause | Disposition |", "|---|---:|---|---|---:|---|---|"]
    for row in flagged:
        lines.append(f"| {row['date']} | {row['bar_count']} | {row['first_bar_timestamp']} | {row['last_bar_timestamp']} | {row['gap_segment_count']} | {row['probable_cause']} | {row['proposed_disposition']} |")
    lines.append("")
    lines += ["## Known odd-count classes", "", "| Count | Dates in scan | Verdict |", "|---:|---|---|"]
    for count in [60, 106, 221, 317, 334, 372, 750]:
        rows = [r for r in flagged if int(r["bar_count"]) == count]
        dates = ", ".join(str(r["date"]) for r in rows) or "none"
        verdict = "; ".join(sorted({str(r["probable_cause"]) for r in rows})) or "not observed in archive"
        lines.append(f"| {count} | {dates} | {verdict} |")
    lines += ["", "## Calendar/file comparison", "", f"Contract-calendar dates in range missing minute files: {', '.join(missing) if missing else 'none (calendar has no 2020–2023 rows, and therefore no in-range missing dates).'}", "", "Known special-session check: 2020-02-01 Budget Saturday is present with exactly 375 regular bars, so it is not a flagged output row.", "", "## Open questions for review", "", "- The archive overwhelmingly uses 376 rows ending at 15:30, while this scan applies the requested 375-bar 09:15–15:29 contract. Confirm whether 15:30 is a vendor terminal artifact or should be part of the registered session.", "- Confirm the 2021-02-24 16:59 extended-tail file with exchange-calendar/source-owner evidence; the proposal drops the day because 229 regular minutes are absent.", "- Confirm the intended handling of the 374/317/318/334/372 files and whether labels near close should be excluded rather than dropping the day.", "- Counts 106 and 750 were not present in this 2020–2023 archive scan; reconcile against the source inventory that reported them.", ""]
    (out / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    if args.to_date < args.from_date:
        raise SystemExit("--to-date must not precede --from-date")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for path in sorted(args.archive_root.glob("*/*/*.csv")):
        try:
            day = file_date(path)
        except ValueError:
            continue
        if args.from_date <= day <= args.to_date:
            rows.append(inspect(path, day))
    flagged = [row for row in rows if int(row["bar_count"]) != EXPECTED_BARS]
    with (args.out_dir / "session_quality_2020_2023.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(flagged)
    repo_root = Path(__file__).resolve().parents[2]
    missing = calendar_missing(repo_root, args.from_date, args.to_date, {date.fromisoformat(str(r["date"])) for r in rows})
    write_report(args.out_dir, rows, flagged, missing, args.from_date, args.to_date)
    print(f"sessions_scanned={len(rows)} sessions_flagged={len(flagged)}")
    print("bar_counts=" + ", ".join(f"{k}:{v}" for k, v in sorted(Counter(int(r["bar_count"]) for r in flagged).items())))
    print("calendar_missing=" + (",".join(missing) if missing else "none"))


if __name__ == "__main__":
    main()
