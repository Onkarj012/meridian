#!/usr/bin/env python3
"""Deduplicate India VIX and prove the point-in-time T-1 join convention."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def vix_asof(clean_df: pd.DataFrame, session_date: object) -> pd.Series | None:
    """Return the latest VIX observation strictly before session_date (T-1)."""
    session = pd.Timestamp(session_date).normalize()
    dates = pd.to_datetime(clean_df["date"]).dt.normalize()
    eligible = clean_df.loc[dates < session]
    return None if eligible.empty else eligible.iloc[-1]


def fmt(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vix", required=True, type=Path)
    parser.add_argument("--calendar", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    raw = pd.read_csv(args.vix, low_memory=False)
    raw["date"] = pd.to_datetime(raw["date"], format="mixed", errors="raise").dt.normalize()
    raw["_order"] = range(len(raw))
    duplicate_dates = raw.loc[raw["date"].duplicated(keep=False), "date"].drop_duplicates().sort_values()
    duplicate_records = []
    for date in duplicate_dates:
        rows = raw[raw["date"].eq(date)].drop(columns=["_order"])
        values = rows.to_dict(orient="records")
        identical = rows.drop(columns=["date"]).nunique(dropna=False).le(1).all()
        duplicate_records.append({"date": date.strftime("%Y-%m-%d"), "rows": [{k: fmt(v) for k, v in row.items()} for row in values], "identical": bool(identical), "kept": "one row" if identical else "last occurrence"})
    clean = raw.sort_values("_order").drop_duplicates("date", keep="last").sort_values("date").drop(columns=["_order"])
    clean["date"] = clean["date"].dt.strftime("%Y-%m-%d")
    clean.to_csv(args.out_dir / "india_vix_clean.csv", index=False)

    calendar = pd.read_csv(args.calendar, usecols=["trade_date"])
    calendar["trade_date"] = pd.to_datetime(calendar["trade_date"])
    clean_dates = set(pd.to_datetime(clean["date"]))
    missing = sorted(set(calendar["trade_date"]) - clean_dates)
    sample_sessions = calendar.sort_values("trade_date").iloc[[0, len(calendar) // 4, len(calendar) // 2, (3 * len(calendar)) // 4, len(calendar) - 1]]["trade_date"]
    samples = []
    for session in sample_sessions:
        row = vix_asof(clean, session)
        samples.append({"session_date": session.strftime("%Y-%m-%d"), "vix_date_used": None if row is None else fmt(row["date"]), "vix_close": None if row is None else fmt(row["close"]), "proof": row is None or pd.Timestamp(row["date"]) < session})

    lines = ["# India VIX report", "", f"Raw rows: {len(raw)}; cleaned rows: {len(clean)}; unique dates: {clean['date'].nunique()}.", "", "## Duplicate dates", ""]
    for duplicate in duplicate_records:
        lines += [f"### {duplicate['date']} — {'identical' if duplicate['identical'] else 'differing'} rows; kept {duplicate['kept']}", "", "```json", pd.Series(duplicate["rows"]).to_json(orient="values", indent=2), "```", ""]
    lines += ["## Missing dates vs trading calendar", "", f"Missing-date count: {len(missing)}.", "", "```text", "\n".join(d.strftime("%Y-%m-%d") for d in missing), "```", "", "## T-1 availability convention", "", "A session D may use only a VIX observation dated strictly before D. The `vix_asof` helper implements the shifted/as-of join by selecting the latest clean VIX date with date < D; same-day VIX is never eligible.", "", "| session date | VIX date used | VIX close | proof |", "| --- | --- | ---: | --- |"]
    lines += [f"| {x['session_date']} | {x['vix_date_used'] or 'none'} | {x['vix_close'] or ''} | {'PASS' if x['proof'] else 'FAIL'} |" for x in samples]
    (args.out_dir / "vix_report.md").write_text("\n".join(lines) + "\n")
    assert len(duplicate_records) == 3, f"Expected 3 duplicate dates, found {len(duplicate_records)}"
    assert clean["date"].is_unique
    assert all(x["proof"] for x in samples)


if __name__ == "__main__":
    main()
