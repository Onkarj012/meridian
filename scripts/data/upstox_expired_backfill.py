#!/usr/bin/env python3
"""Resumable Upstox expired-NIFTY-future minute-bar backfill."""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ingest.upstox_expired import (
    UpstoxApiError, access_token_from_env, candles_to_futures_csv, contract_key,
    expired_key, fetch_minute_candles, get_future_contract,
    minute_file_path, split_by_day, validate_day_file, write_day_csv,
)
from ingest.expired_backfill import build_plan, load_target_rows, print_plan, write_report

DEFAULT_OUT_ROOT = os.environ.get("DATA_OUT_ROOT")
DEFAULT_REPORT = Path("runs/sleeve-f-data-contract/backfill_report.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="from_date")
    parser.add_argument("--to", dest="to_date")
    parser.add_argument("--calendar", required=True)
    parser.add_argument("--gap-days")
    parser.add_argument("--out-root", default=DEFAULT_OUT_ROOT, required=DEFAULT_OUT_ROOT is None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    args = parser.parse_args(argv)

    rows = load_target_rows(args.calendar, args.gap_days, args.from_date, args.to_date, args.out_root, validate_only=args.validate_only)
    plan = build_plan(rows)
    print_plan("Upstox", plan)
    report: dict[str, Any] = {
        "fetched": [], "skipped": [], "failed": [],
        "validation_results": [], "validation_failures": [],
    }
    if args.dry_run:
        print("dry-run: no network calls or archive writes")
        write_report(report, args.report)
        return 0

    if args.validate_only:
        for row in rows:
            validation = validate_day_file(minute_file_path(row["trade_date"], args.out_root), row)
            report["validation_results"].append(validation)
            if not validation["ok"]:
                report["validation_failures"].append(validation)
        write_report(report, args.report)
        print(f"validation: {len(rows) - len(report['validation_failures'])} passed, {len(report['validation_failures'])} failed")
        return 1 if report["validation_failures"] else 0

    token = access_token_from_env()
    for (exchange_token, expiry), days in plan.items():
        try:
            contract = get_future_contract(expiry, access_token=token)
            supplied_key = contract_key(contract, exchange_token, expiry)
            constructed_key = expired_key(exchange_token, expiry)
            start, end = min(row["trade_date"] for row in days), max(row["trade_date"] for row in days)
            try:
                candles = fetch_minute_candles(supplied_key or constructed_key, start, end, access_token=token)
            except UpstoxApiError as exc:
                if supplied_key and supplied_key != constructed_key and exc.code == "UDAPI1021":
                    candles = fetch_minute_candles(constructed_key, start, end, access_token=token)
                else:
                    raise
            by_day = split_by_day(candles_to_futures_csv(candles))
            for row in days:
                trade_day = date.fromisoformat(row["trade_date"])
                path = minute_file_path(trade_day, args.out_root)
                if path.exists() and not args.overwrite:
                    report["skipped"].append({"trade_date": row["trade_date"], "reason": "exists"})
                elif trade_day not in by_day:
                    report["failed"].append({"trade_date": row["trade_date"], "reason": "no_market_hours_candles"})
                    continue
                else:
                    write_day_csv(by_day[trade_day], trade_day, args.out_root, overwrite=args.overwrite)
                    report["fetched"].append({"trade_date": row["trade_date"], "path": str(path)})
                validation = validate_day_file(path, row)
                report["validation_results"].append(validation)
                if not validation["ok"]:
                    report["validation_failures"].append(validation)
        except Exception as exc:  # preserve remaining contracts for resumability
            succeeded_days = {
                str(item["trade_date"])
                for item in report["validation_results"]
                if item.get("ok")
            }
            failed_days = {
                str(item["trade_date"])
                for item in report["failed"] + report["validation_failures"]
            }
            for row in days:
                if row["trade_date"] not in succeeded_days and row["trade_date"] not in failed_days:
                    report["failed"].append({"trade_date": row["trade_date"], "contract": f"{exchange_token}/{expiry}", "reason": str(exc)})
    write_report(report, args.report)
    print(f"summary: fetched={len(report['fetched'])} skipped={len(report['skipped'])} failed={len(report['failed'])} validation_failures={len(report['validation_failures'])}")
    return 1 if report["failed"] or report["validation_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
