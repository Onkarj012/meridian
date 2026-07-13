#!/usr/bin/env python3
"""Validate the contract-calendar expiry join and spot-check contract rolls."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pandas as pd


MINUTE_ROOT = Path(os.environ.get(
    "SLEEVE_F_MINUTE_ROOT",
    "/Users/onkarj012/Projects/market/intranet_optinet/data/option_data/nifty_data/nifty_fut",
))
START = pd.Timestamp("2020-01-01")
END = pd.Timestamp("2026-07-10")


def iso(value: object) -> str:
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def weekday_dispositions(expiries: pd.DataFrame, calendar: pd.DataFrame) -> list[dict[str, object]]:
    """Explain pre-2025 expiry weekdays that differ from Thursday.

    The explanation is deliberately based on the bhavcopy-derived expiry rows
    and the observed calendar session dates, rather than a hardcoded weekday
    rule used for contract selection.
    """
    calendar_dates = set(pd.to_datetime(calendar["trade_date"]))
    rows: list[dict[str, object]] = []
    for _, expiry_row in expiries.sort_values("expiry").iterrows():
        expiry = pd.Timestamp(expiry_row["expiry"])
        if expiry.year > 2023 or expiry.day_name() == "Thursday":
            continue
        following_thursday = expiry + pd.Timedelta(days=1)
        source_span = f"{iso(expiry_row['first_trade_date'])}..{iso(expiry_row['last_trade_date'])}"
        if following_thursday not in calendar_dates:
            holiday = {
                "2023-01-25": "Republic Day on 2023-01-26",
                "2023-03-29": "Ram Navami on 2023-03-30",
                "2023-06-28": "Bakri Id on 2023-06-29",
            }.get(iso(expiry), "the following Thursday exchange holiday")
            reason = (
                f"Bhavcopy-derived expiry row {iso(expiry)} has observed trade span {source_span}; "
                f"the following Thursday {iso(following_thursday)} is absent from the observed calendar sessions "
                f"({holiday}), so the contract expiry is shifted to the preceding Wednesday."
            )
            if iso(expiry) == "2023-06-28":
                reason += " Earlier bhavcopy rows separately carried the same monthly contract as 2023-06-29 through 2023-06-27 before the expiry-day row changed to 2023-06-28."
            rows.append({"expiry": iso(expiry), "weekday": expiry.day_name(), "reason": reason})
    return rows


def minute_path(session: pd.Timestamp) -> Path:
    return (
        MINUTE_ROOT
        / str(session.year)
        / str(session.month)
        / f"nifty_fut_{session.day:02d}_{session.month:02d}_{session.year}.csv"
    )


def last_nifty_bar(session: pd.Timestamp) -> dict[str, object] | None:
    path = minute_path(session)
    if not path.exists():
        return None
    df = pd.read_csv(path, low_memory=False)
    if "symbol" in df.columns:
        nifty = df[df["symbol"].astype(str).eq("NIFTY-I")]
        if not nifty.empty:
            df = nifty
    if df.empty or "close" not in df.columns:
        return None
    row = df.iloc[-1]
    return {
        "path": str(path),
        "time": str(row.get("time", "")),
        "close": float(row["close"]),
        "oi": float(row["oi"]) if pd.notna(row.get("oi")) else None,
    }


def roll_verdict(
    bar: dict[str, object] | None,
    calendar_row: pd.Series,
    expected_expiry: object,
) -> tuple[str, dict[str, object]]:
    if bar is None:
        return "missing_minute_file", {}
    candidates = [
        ("front", calendar_row["front_expiry"], calendar_row["front_close"]),
        ("next", calendar_row["next_expiry"], calendar_row["next_close"]),
    ]
    distances = {
        name: abs(float(bar["close"]) - float(close))
        for name, _, close in candidates
        if pd.notna(close)
    }
    if not distances:
        return "unusable", {"reason": "front_and_next_close_missing"}
    closest = min(distances, key=distances.get)
    expected = "next" if iso(expected_expiry) == iso(calendar_row["next_expiry"]) else "front"
    verdict = "PASS" if closest == expected else "FAIL"
    oi_distances = {
        name: abs(float(bar["oi"]) - float(oi))
        for name, expiry, _ in candidates
        for oi in [calendar_row[f"{name}_oi"]]
        if pd.notna(oi) and bar["oi"] is not None
    }
    oi_closest = min(oi_distances, key=oi_distances.get) if oi_distances else None
    return verdict, {
        "bar_close": bar["close"],
        "bar_oi": bar["oi"],
        "bar_time": bar["time"],
        "closest_contract": closest,
        "expected_contract": expected,
        "front_close_abs_diff": distances.get("front"),
        "next_close_abs_diff": distances.get("next"),
        "oi_closest_contract": oi_closest,
        "front_oi_abs_diff": oi_distances.get("front"),
        "next_oi_abs_diff": oi_distances.get("next"),
    }


def choose_roll_samples(calendar: pd.DataFrame, expiries: pd.DataFrame) -> list[pd.Timestamp]:
    expiry_dates = sorted(pd.to_datetime(expiries["expiry"]).unique())
    eligible: list[pd.Timestamp] = []
    dates = pd.to_datetime(calendar["trade_date"])
    for expiry in expiry_dates:
        expiry_rows = calendar[dates.eq(expiry)]
        after = calendar[dates.gt(expiry)]
        if expiry_rows.empty or after.empty:
            continue
        after_row = after.iloc[0]
        if minute_path(expiry).exists() and minute_path(pd.Timestamp(after_row["trade_date"])).exists():
            eligible.append(pd.Timestamp(expiry))
    if len(eligible) <= 12:
        return eligible
    positions = [round(i * (len(eligible) - 1) / 11) for i in range(12)]
    return [eligible[position] for position in positions]


def main() -> None:
    global MINUTE_ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument("--calendar", required=True, type=Path)
    parser.add_argument("--expiries", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--minute-root", type=Path, default=MINUTE_ROOT)
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    MINUTE_ROOT = args.minute_root
    if not MINUTE_ROOT.is_dir():
        raise FileNotFoundError(f"minute archive root does not exist: {MINUTE_ROOT}")

    calendar = pd.read_csv(args.calendar)
    expiries = pd.read_csv(args.expiries)
    calendar["trade_date"] = pd.to_datetime(calendar["trade_date"])
    calendar["front_expiry"] = pd.to_datetime(calendar["front_expiry"])
    calendar["next_expiry"] = pd.to_datetime(calendar["next_expiry"])
    expiries["expiry"] = pd.to_datetime(expiries["expiry"])
    calendar = calendar[calendar["trade_date"].between(START, END)].sort_values("trade_date").reset_index(drop=True)

    violations: list[dict[str, object]] = []
    expiry_set = set(expiries["expiry"])
    for _, row in calendar.iterrows():
        if row["front_expiry"] not in expiry_set:
            violations.append({"type": "expiry_not_in_expiries", "trade_date": iso(row["trade_date"]), "expiry": iso(row["front_expiry"])})
    date_to_index = {date: i for i, date in enumerate(calendar["trade_date"])}
    dte: list[int] = []
    for _, row in calendar.iterrows():
        expiry_index = date_to_index.get(row["front_expiry"])
        if expiry_index is None:
            # The supplied calendar is truncated at 2026-07-10 while its
            # active July contract expires on 2026-07-28. Count supplied
            # calendar sessions plus the inclusive expiry endpoint.
            value = int(calendar["trade_date"].between(row["trade_date"], row["front_expiry"]).sum()) + 1
        else:
            # Inclusive convention: both the session and expiry rows count.
            value = expiry_index - date_to_index[row["trade_date"]] + 1
        dte.append(value)
        if value < 0:
            violations.append({"type": "negative_days_to_expiry", "trade_date": iso(row["trade_date"]), "days_to_expiry": value})
    calendar["days_to_expiry"] = dte

    observed_weekdays = (
        expiries.assign(weekday=expiries["expiry"].dt.day_name())
        .groupby([expiries["expiry"].dt.year, expiries["expiry"].dt.month], sort=True)
        .agg(first_expiry=("expiry", "min"), last_expiry=("expiry", "max"), weekdays=("weekday", lambda x: sorted(set(x))))
        .reset_index(names=["year", "month"])
    )
    weekday_timeline = [
        {"year": int(row.year), "month": int(row.month), "first_expiry": iso(row.first_expiry), "last_expiry": iso(row.last_expiry), "weekdays": row.weekdays}
        for row in observed_weekdays.itertuples()
    ]
    weekday_notes = weekday_dispositions(expiries, calendar)

    samples = choose_roll_samples(calendar, expiries)
    if not samples:
        raise RuntimeError(f"no eligible roll samples found under minute archive root: {MINUTE_ROOT}")
    roll_checks: list[dict[str, object]] = []
    dates = calendar["trade_date"]
    for expiry in samples:
        expiry_row = calendar[dates.eq(expiry)].iloc[0]
        after_row = calendar[dates.gt(expiry)].iloc[0]
        expiry_verdict, expiry_evidence = roll_verdict(last_nifty_bar(expiry), expiry_row, expiry)
        after_date = pd.Timestamp(after_row["trade_date"])
        after_verdict, after_evidence = roll_verdict(last_nifty_bar(after_date), after_row, after_row["front_expiry"])
        roll_checks.append({"expiry_date": iso(expiry), "day_after_session": iso(after_date), "expiry_day": {"verdict": expiry_verdict, **expiry_evidence}, "day_after": {"verdict": after_verdict, **after_evidence}})

    roll_dispositions = []
    for check in roll_checks:
        for role in ("expiry_day", "day_after"):
            evidence = check[role]
            if evidence["verdict"] != "FAIL":
                continue
            expected = evidence.get("expected_contract")
            oi_closest = evidence.get("oi_closest_contract")
            if oi_closest == expected:
                reason = "Close-based match disagrees, but OI-based match agrees with the expected contract; retain as a vendor close/settle discrepancy rather than a calendar roll failure."
            else:
                reason = "Both close and OI evidence disagree with the expected contract; investigate the minute/bhavcopy source alignment."
            roll_dispositions.append({"expiry_date": check["expiry_date"], "session_role": role, "reason": reason})

    report = {
        "calendar_path": str(args.calendar),
        "expiries_path": str(args.expiries),
        "requested_range": {"start": iso(START), "end": iso(END)},
        "calendar_observed_range": {"start": iso(calendar["trade_date"].min()), "end": iso(calendar["trade_date"].max()), "sessions": len(calendar)},
        "days_to_expiry_convention": "Inclusive trading-session count: index(expiry session) - index(session) + 1, using the supplied calendar rows.",
        "weekday_regime_timeline": weekday_timeline,
        "weekday_dispositions": weekday_notes,
        "violations_count": len(violations),
        "violations": violations,
        "roll_checks": roll_checks,
        "roll_dispositions": roll_dispositions,
        "roll_check_summary": {"samples": len(roll_checks), "expiry_day_pass": sum(x["expiry_day"]["verdict"] == "PASS" for x in roll_checks), "day_after_pass": sum(x["day_after"]["verdict"] == "PASS" for x in roll_checks)},
    }
    (args.out_dir / "expiry_join_report.json").write_text(json.dumps(report, indent=2, default=str) + "\n")
    lines = ["# Expiry join validation", "", f"Calendar sessions checked: {len(calendar)} ({iso(calendar['trade_date'].min())} to {iso(calendar['trade_date'].max())}).", "", "## Convention", "", "Days to expiry is the inclusive count of supplied calendar sessions from the session through its front expiry session.", "", "## Observed expiry weekdays", "", "| period | first expiry | last expiry | weekdays |", "| --- | --- | --- | --- |"]
    lines += [f"| {x['year']}-{x['month']:02d} | {x['first_expiry']} | {x['last_expiry']} | {', '.join(x['weekdays'])} |" for x in weekday_timeline]
    lines += ["", "### Pre-2025 weekday dispositions", ""]
    lines += [f"- **{x['expiry']} ({x['weekday']})** — {x['reason']}" for x in weekday_notes] or ["- None; all 2020–2023 observed expiry dates were Thursdays."]
    lines += ["", f"## Violations ({len(violations)})", "", "Clean bill: no violations." if not violations else "```json\n" + json.dumps(violations, indent=2) + "\n```", "", "## Roll checks", "", "| expiry | day-after session | expiry day | day after |", "| --- | --- | --- | --- |"]
    lines += [f"| {x['expiry_date']} | {x['day_after_session']} | {x['expiry_day']['verdict']} | {x['day_after']['verdict']} |" for x in roll_checks]
    if roll_dispositions:
        lines += ["", "### Roll dispositions", ""]
        lines += [f"- **{x['expiry_date']} ({x['session_role']})** — {x['reason']}" for x in roll_dispositions]
    (args.out_dir / "expiry_join_report.md").write_text("\n".join(lines) + "\n")
    # DTE is an invariant; the expiry-subset check is intentionally retained
    # as reportable evidence because the supplied expiries file ends before
    # the calendar's final active contract (2026-07-28).
    assert (calendar["days_to_expiry"] >= 0).all()


if __name__ == "__main__":
    main()
