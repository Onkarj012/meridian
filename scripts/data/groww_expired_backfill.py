#!/usr/bin/env python3
"""Resumable Groww expired-NIFTY-future minute-bar backfill."""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ingest.envfile import load_env
from ingest.expired_backfill import build_plan, load_target_rows, print_plan, write_report
from ingest.groww_expired import (
    candles_to_futures_csv,
    fetch_minute_candles,
    get_access_token,
    resolve_future_contract,
    GROWW_OI_VALIDATION_RATIO_BOUNDS,
    minute_file_path,
    probe,
    split_by_day,
    validate_day_file,
    urllib_transport,
    write_day_csv,
)

DEFAULT_OUT_ROOT = Path("/Users/onkarj012/Projects/market/intranet_optinet/data/option_data/nifty_data/nifty_fut")
DEFAULT_REPORT = Path("runs/sleeve-f-data-contract/groww_backfill_report.json")


def main(argv: list[str] | None = None) -> int:
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="from_date")
    parser.add_argument("--to", dest="to_date")
    parser.add_argument("--calendar")
    parser.add_argument("--gap-days")
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--probe", metavar="EXPIRY_DATE")
    args = parser.parse_args(argv)

    if args.probe:
        try:
            token = get_access_token(urllib_transport, os.environ.get("GROWW_API_KEY"), os.environ.get("GROWW_API_SECRET"))
            probe(args.probe, access_token=token, transport=urllib_transport)
            return 0
        except Exception as exc:
            print(f"probe failed: {exc}", file=sys.stderr)
            return 1

    if not args.calendar:
        parser.error("--calendar is required unless --probe is used")
    rows = load_target_rows(args.calendar, args.gap_days, args.from_date, args.to_date, args.out_root, validate_only=args.validate_only)
    plan = build_plan(rows)
    print_plan("Groww", plan)
    report: dict[str, Any] = {
        "fetched": [], "skipped": [], "failed": [],
        "validation_results": [], "validation_failures": [], "warnings": [],
    }
    if args.dry_run:
        print("dry-run: no network calls or archive writes")
        write_report(report, args.report)
        return 0

    if args.validate_only:
        for row in rows:
            validation = validate_day_file(
                minute_file_path(row["trade_date"], args.out_root), row,
                oi_ratio_bounds=GROWW_OI_VALIDATION_RATIO_BOUNDS,
            )
            report["validation_results"].append(validation)
            if not validation["ok"]:
                report["validation_failures"].append(validation)
        report["validation_summary"] = {
            "total": len(report["validation_results"]),
            "passed": sum(result["ok"] and not result.get("skipped") for result in report["validation_results"]),
            "failed": sum(not result["ok"] for result in report["validation_results"]),
            "skipped": sum(result.get("skipped", False) for result in report["validation_results"]),
        }
        write_report(report, args.report)
        print(
            "validation: "
            f"{report['validation_summary']['passed']} passed, "
            f"{report['validation_summary']['failed']} failed, "
            f"{report['validation_summary']['skipped']} skipped"
        )
        return 1 if report["validation_failures"] else 0

    try:
        token = get_access_token(urllib_transport, os.environ.get("GROWW_API_KEY"), os.environ.get("GROWW_API_SECRET"))
    except Exception as exc:
        print(f"authentication failed: {exc}", file=sys.stderr)
        return 1

    for (_exchange_token, expiry), days in plan.items():
        try:
            contract = resolve_future_contract(expiry, access_token=token, transport=urllib_transport)
            start = min(row["trade_date"] for row in days)
            end = max(row["trade_date"] for row in days)
            candles = fetch_minute_candles(contract, start, end, access_token=token, transport=urllib_transport)
            calendar_rows = {row["trade_date"]: row for row in days}
            converted = candles_to_futures_csv(candles, calendar_rows=calendar_rows)
            for trade_day in converted.attrs.get("groww_oi_rescaled_dates", []):
                report["warnings"].append({
                    "trade_date": trade_day,
                    "note": "groww_oi_rescaled_x100",
                    "message": "Groww raw OI was normalized ×100 using the measured 2025-01-01 regime; verify against the calendar ratio.",
                })
            by_day = split_by_day(converted)
            for row in days:
                trade_day = row["trade_date"]
                path = minute_file_path(trade_day, args.out_root)
                if path.exists() and not args.overwrite:
                    report["skipped"].append({"trade_date": trade_day, "reason": "exists"})
                elif date.fromisoformat(trade_day) not in by_day:
                    report["failed"].append({"trade_date": trade_day, "reason": "no_market_hours_candles"})
                    continue
                else:
                    write_day_csv(by_day[date.fromisoformat(trade_day)], trade_day, args.out_root, overwrite=args.overwrite)
                    report["fetched"].append({"trade_date": trade_day, "path": str(path), "contract": contract})
                validation = validate_day_file(path, row, oi_ratio_bounds=GROWW_OI_VALIDATION_RATIO_BOUNDS)
                report["validation_results"].append(validation)
                if not validation["ok"]:
                    report["validation_failures"].append(validation)
        except Exception as exc:  # preserve remaining contracts for resumability
            for row in days:
                report["failed"].append({"trade_date": row["trade_date"], "contract": f"{expiry}", "reason": str(exc)})
    write_report(report, args.report)
    print(f"summary: fetched={len(report['fetched'])} skipped={len(report['skipped'])} failed={len(report['failed'])} validation_failures={len(report['validation_failures'])}")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
