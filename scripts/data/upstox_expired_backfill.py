#!/usr/bin/env python3
"""Resumable Upstox expired-NIFTY-future minute-bar backfill."""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ingest.upstox_expired import (
    UpstoxApiError, access_token_from_env, candles_to_futures_csv, contract_key,
    expired_key, fetch_minute_candles, get_future_contract, minute_file_path,
    split_by_day, validate_day_file, write_day_csv,
)

DEFAULT_OUT_ROOT = Path("/Users/onkarj012/Projects/market/intranet_optinet/data/option_data/nifty_data/nifty_fut")
DEFAULT_REPORT = Path("runs/sleeve-f-data-contract/backfill_report.json")


def _date_text(value: Any) -> str:
    return pd.Timestamp(value).date().isoformat()


def _token(value: Any) -> str:
    value = str(value).strip()
    return value[:-2] if value.endswith(".0") and value[:-2].isdigit() else value


def load_target_rows(calendar_path: str | Path, gap_days_path: str | Path | None, from_date: str | None, to_date: str | None, out_root: str | Path, *, validate_only: bool = False) -> list[dict[str, Any]]:
    """Load selected trade days, using gaps when supplied or missing files otherwise."""
    calendar = pd.read_csv(calendar_path, dtype={"front_instrument_id": str, "next_instrument_id": str})
    calendar["trade_date"] = pd.to_datetime(calendar["trade_date"]).dt.date
    if gap_days_path:
        gaps = pd.read_csv(gap_days_path, dtype={"front_instrument_id": str})
        gaps["trade_date"] = pd.to_datetime(gaps["trade_date"]).dt.date
        rows = gaps.merge(calendar, on="trade_date", how="left", suffixes=("", "_calendar"))
        for column in ("front_instrument_id", "front_expiry"):
            backup = f"{column}_calendar"
            if backup in rows:
                rows[column] = rows[column].fillna(rows[backup])
    else:
        rows = calendar.copy()
    if from_date:
        rows = rows[rows["trade_date"] >= date.fromisoformat(from_date)]
    if to_date:
        rows = rows[rows["trade_date"] <= date.fromisoformat(to_date)]
    result = []
    for row in rows.to_dict("records"):
        if not row.get("front_instrument_id") or not row.get("front_expiry"):
            continue
        row["trade_date"] = _date_text(row["trade_date"])
        row["front_expiry"] = _date_text(row["front_expiry"])
        row["front_instrument_id"] = _token(row["front_instrument_id"])
        exists = minute_file_path(row["trade_date"], out_root).exists()
        if validate_only or gap_days_path or not exists:
            result.append(row)
    return result


def build_plan(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    plan: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        plan[(row["front_instrument_id"], row["front_expiry"])].append(row)
    return dict(plan)


def _write_report(report: dict[str, Any], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="from_date")
    parser.add_argument("--to", dest="to_date")
    parser.add_argument("--calendar", required=True)
    parser.add_argument("--gap-days")
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    args = parser.parse_args(argv)

    rows = load_target_rows(args.calendar, args.gap_days, args.from_date, args.to_date, args.out_root, validate_only=args.validate_only)
    plan = build_plan(rows)
    print(f"Upstox expired futures backfill: {len(rows)} days in {len(plan)} contracts")
    for (token, expiry), days in sorted(plan.items()):
        print(f"  token={token} expiry={expiry}: {', '.join(row['trade_date'] for row in days)}")
    report: dict[str, Any] = {"fetched": [], "skipped": [], "failed": [], "validation_failures": []}
    if args.dry_run:
        print("dry-run: no network calls or archive writes")
        _write_report(report, args.report)
        return 0

    if args.validate_only:
        for row in rows:
            validation = validate_day_file(minute_file_path(row["trade_date"], args.out_root), row)
            if not validation["ok"]:
                report["validation_failures"].append(validation)
        _write_report(report, args.report)
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
                if not validation["ok"]:
                    report["validation_failures"].append(validation)
        except Exception as exc:  # preserve remaining contracts for resumability
            for row in days:
                report["failed"].append({"trade_date": row["trade_date"], "contract": f"{exchange_token}/{expiry}", "reason": str(exc)})
    _write_report(report, args.report)
    print(f"summary: fetched={len(report['fetched'])} skipped={len(report['skipped'])} failed={len(report['failed'])} validation_failures={len(report['validation_failures'])}")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
