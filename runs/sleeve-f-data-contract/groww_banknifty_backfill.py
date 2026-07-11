#!/usr/bin/env python3
"""Backfill expired BankNifty front-month futures from Groww."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ingest.envfile import load_env
from ingest import groww_expired
from ingest.expired_common import (
    BAR_COUNT_MAX,
    BAR_COUNT_MIN,
    CLOSE_TOLERANCE,
    IST,
    VOLUME_TOLERANCE,
    OI_TOLERANCE,
    candles_to_futures_csv as common_candles_to_futures_csv,
    split_by_day,
)


PROJECT_ROOT = Path(os.environ.get("PROJECT_ROOT", "/Users/onkarj012/Projects/market/meridian"))
DEFAULT_CALENDAR = PROJECT_ROOT / "runs/sleeve-f-data-contract/banknifty_contract_calendar.csv"
DEFAULT_GAPS = PROJECT_ROOT / "runs/sleeve-f-data-contract/banknifty_gap_days.csv"
DEFAULT_OUT_ROOT = Path(os.environ.get("BANKNIFTY_OUT_ROOT", "/Users/onkarj012/Projects/market/intranet_optinet/data/option_data/banknifty_data/banknifty_fut"))
DEFAULT_REPORT = PROJECT_ROOT / "runs/sleeve-f-data-contract/banknifty_backfill_report.json"
UNDERLYING = "BANKNIFTY"
EXCHANGE = "NSE"
SEGMENT = "FNO"
ARCHIVE_FILE_RE = re.compile(r"banknifty_fut_(\d{2})_(\d{2})_(\d{4})\.csv$")
KNOWN_MUHURAT_DATES = {"2024-11-01", "2025-10-21"}


def banknifty_file_path(day: str | date, root: str | Path = DEFAULT_OUT_ROOT) -> Path:
    trade_day = pd.Timestamp(day).date()
    return Path(root) / str(trade_day.year) / str(trade_day.month) / f"banknifty_fut_{trade_day:%d_%m_%Y}.csv"


def write_banknifty_day(frame: pd.DataFrame, day: str | date, root: str | Path, *, overwrite: bool = False) -> Path | None:
    path = banknifty_file_path(day, root)
    if path.exists() and not overwrite:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return path


def date_text(value: Any) -> str:
    return pd.Timestamp(value).date().isoformat()


def load_rows(calendar_path: Path, gaps_path: Path, from_date: str | None, to_date: str | None) -> list[dict[str, Any]]:
    calendar = pd.read_csv(calendar_path, dtype={"front_instrument_id": str, "next_instrument_id": str})
    gaps = pd.read_csv(gaps_path, dtype={"front_instrument_id": str})
    rows = gaps.merge(calendar, on="trade_date", how="left", suffixes=("", "_calendar"))
    for column in ("front_instrument_id", "front_expiry", "front_close", "front_volume", "front_oi"):
        backup = f"{column}_calendar"
        if backup in rows:
            rows[column] = rows[column].fillna(rows[backup])
    rows["trade_date"] = pd.to_datetime(rows["trade_date"]).dt.date
    if from_date:
        rows = rows[rows["trade_date"] >= date.fromisoformat(from_date)]
    if to_date:
        rows = rows[rows["trade_date"] <= date.fromisoformat(to_date)]
    result = []
    for row in rows.to_dict("records"):
        row["trade_date"] = date_text(row["trade_date"])
        row["front_expiry"] = date_text(row["front_expiry"])
        row["bhavcopy_expected"] = bool(row.get("bhavcopy_expected", True))
        result.append(row)
    return result


def request_path(path: str, params: dict[str, Any]) -> str:
    return f"{path}?{urlencode(params)}"


def list_bank_contracts(expiry: str, *, access_token: str) -> tuple[list[str], Any]:
    response = groww_expired._request_json(
        request_path(groww_expired.HISTORICAL_CONTRACTS_PATH, {
            "exchange": EXCHANGE, "underlying_symbol": UNDERLYING, "expiry_date": expiry,
        }),
        access_token=access_token,
        transport=groww_expired.urllib_transport,
    )
    contracts = response.get("payload", {}).get("contracts") if isinstance(response, dict) else None
    if not isinstance(contracts, list) or not all(isinstance(value, str) for value in contracts):
        raise groww_expired.GrowwApiError(
            f"BankNifty Get Contracts response shape differs; response={response!r}"
        )
    return contracts, response


def list_bank_expiries(expiry: str, *, access_token: str) -> Any:
    day = pd.Timestamp(expiry)
    response = groww_expired._request_json(
        request_path(groww_expired.HISTORICAL_EXPIRIES_PATH, {
            "exchange": EXCHANGE, "underlying_symbol": UNDERLYING, "year": day.year, "month": day.month,
        }),
        access_token=access_token,
        transport=groww_expired.urllib_transport,
    )
    expiries = response.get("payload", {}).get("expiries") if isinstance(response, dict) else None
    if not isinstance(expiries, list) or not all(isinstance(value, str) for value in expiries):
        raise groww_expired.GrowwApiError(f"BankNifty Get Expiries response shape differs; response={response!r}")
    if expiry not in expiries:
        raise groww_expired.GrowwApiError(
            f"BankNifty Get Expiries did not return requested expiry {expiry}; response={response!r}"
        )
    return response


def resolve_bank_contract(expiry: str, *, access_token: str) -> tuple[str, dict[str, Any]]:
    expiry_response = list_bank_expiries(expiry, access_token=access_token)
    contracts, response = list_bank_contracts(expiry, access_token=access_token)
    futures = [value for value in contracts if value.endswith("-FUT")]
    if futures:
        return futures[0], {"mode": "get_contracts", "contract_count": len(contracts), "response_status": response.get("status"), "expiry_response_status": expiry_response.get("status")}
    # The documented contracts response has historically shown options only.
    # Preserve the exact discovery response and try the documented futures
    # symbol format as a bounded fallback.
    expiry_symbol = pd.Timestamp(expiry).strftime("%d%b%y")
    fallback = f"NSE-BANKNIFTY-{expiry_symbol}-FUT"
    return fallback, {
        "mode": "constructed_fallback",
        "contract_count": len(contracts),
        "response_status": response.get("status"),
        "expiry_response_status": expiry_response.get("status"),
        "note": "Get Contracts returned no -FUT entry; constructed documented futures symbol",
    }


def convert_candles(candles: list[list[Any]]) -> pd.DataFrame:
    frame = groww_expired.candles_to_frame(candles)
    converted = common_candles_to_futures_csv(frame)
    if not converted.empty:
        converted["symbol"] = "BANKNIFTY-I"
    return converted


def last_nonzero_oi(frame: pd.DataFrame) -> float | None:
    oi = pd.to_numeric(frame["oi"], errors="coerce")
    nonzero = oi[oi.ne(0) & oi.notna()]
    return float(nonzero.iloc[-1]) if not nonzero.empty else None


def numeric(value: Any) -> float | None:
    if value is None or pd.isna(value) or str(value).strip() == "":
        return None
    return float(value)


def infer_oi_regime(raw_frames: dict[str, pd.DataFrame], rows_by_date: dict[str, dict[str, Any]]) -> tuple[dict[str, str], list[dict[str, Any]], dict[str, Any]]:
    regimes: dict[str, str] = {}
    observations: list[dict[str, Any]] = []
    for day, frame in sorted(raw_frames.items()):
        row = rows_by_date.get(day, {})
        expected = numeric(row.get("front_oi")) if row.get("bhavcopy_expected", True) else None
        raw = last_nonzero_oi(frame)
        ratio = expected / raw if expected is not None and raw not in (None, 0) else None
        regime = "unknown"
        if ratio is not None:
            if 0.5 <= ratio <= 2.0:
                regime = "near_one"
            elif ratio >= 10.0:
                regime = "x100_raw_oi"
            else:
                regime = "ambiguous"
        regimes[day] = regime
        observations.append({"trade_date": day, "expected_oi": expected, "raw_last_nonzero_oi": raw, "ratio_expected_to_raw": ratio, "regime": regime})
    x100_days = [item["trade_date"] for item in observations if item["regime"] == "x100_raw_oi"]
    near_one_days = [item["trade_date"] for item in observations if item["regime"] == "near_one"]
    boundary = {
        "method": "daily expected_bhavcopy_oi / raw_last_nonzero_oi; near_one=0.5..2, x100>=10",
        "first_x100_raw_oi_date": min(x100_days) if x100_days else None,
        "last_near_one_date_before_x100": max((day for day in near_one_days if x100_days and day < min(x100_days)), default=None),
        "near_one_days": len(near_one_days),
        "x100_raw_oi_days": len(x100_days),
        "ambiguous_days": sum(item["regime"] == "ambiguous" for item in observations),
        "unknown_days": sum(item["regime"] == "unknown" for item in observations),
    }
    return regimes, observations, boundary


def apply_oi_scaling(raw_frames: dict[str, pd.DataFrame], regimes: dict[str, str]) -> tuple[dict[str, pd.DataFrame], list[dict[str, Any]]]:
    scaled: dict[str, pd.DataFrame] = {}
    events: list[dict[str, Any]] = []
    for day, frame in raw_frames.items():
        work = frame.copy()
        if regimes.get(day) == "x100_raw_oi":
            work["oi"] = pd.to_numeric(work["oi"], errors="coerce") * 100
            events.append({"trade_date": day, "factor": 100, "reason": "empirical_banknifty_calendar_ratio"})
        scaled[day] = work
    return scaled, events


def derive_lots(raw_frames: dict[str, pd.DataFrame], rows_by_date: dict[str, dict[str, Any]]) -> tuple[dict[str, int], dict[str, Any]]:
    samples: dict[str, list[float]] = defaultdict(list)
    for day, frame in raw_frames.items():
        row = rows_by_date.get(day, {})
        expected = numeric(row.get("front_volume")) if row.get("bhavcopy_expected", True) else None
        if expected is None or expected == 0:
            continue
        ratio = float(pd.to_numeric(frame["volume"], errors="coerce").sum()) / expected
        if pd.notna(ratio) and ratio > 0:
            samples[row["front_expiry"]].append(ratio)
    lots: dict[str, int] = {}
    table: dict[str, Any] = {}
    for expiry, values in sorted(samples.items()):
        median = float(pd.Series(values).median())
        lot = max(1, int(round(median)))
        lots[expiry] = lot
        table[expiry] = {"derived_lot_size": lot, "sample_count": len(values), "median_volume_ratio": median, "min_ratio": min(values), "max_ratio": max(values), "samples": values}
    return lots, table


def validate_file(path: Path, row: dict[str, Any], lot: int | None) -> dict[str, Any]:
    day = row["trade_date"]
    report: dict[str, Any] = {"trade_date": day, "path": str(path), "ok": True, "failures": [], "notes": []}
    if day in KNOWN_MUHURAT_DATES:
        report["skipped"] = True
        report["notes"].append("muhurat_no_regular_session_validation")
        if path.exists():
            report["bar_count"] = len(pd.read_csv(path))
        return report
    if not path.exists():
        return {**report, "ok": False, "failures": ["missing_file"]}
    frame = pd.read_csv(path)
    report["bar_count"] = len(frame)
    if not BAR_COUNT_MIN <= len(frame) <= BAR_COUNT_MAX:
        report["failures"].append("bar_count")
    if frame.empty:
        return {**report, "ok": False, "failures": report["failures"] + ["close", "volume", "oi"]}
    volumes = pd.to_numeric(frame["volume"], errors="coerce")
    closes = pd.to_numeric(frame["close"], errors="coerce")
    oi = pd.to_numeric(frame["oi"], errors="coerce")
    if str(frame["time"].iloc[-1]) < "15:25:00":
        report["notes"].append("truncated_day")
        report["failures"].append("session_truncated")
    close_window = frame.loc[(frame["time"].astype(str) >= "15:00:00") & (volumes > 0) & closes.notna()]
    actual_close = None
    if not close_window.empty and float(volumes.loc[close_window.index].sum()) > 0:
        weights = volumes.loc[close_window.index]
        actual_close = float((closes.loc[close_window.index] * weights).sum() / weights.sum())
    actual_volume = float(volumes.sum())
    actual_oi = last_nonzero_oi(frame)
    report.update(close_vwap=actual_close, volume=actual_volume, close_oi=actual_oi, lot_size=lot)
    expected_close = numeric(row.get("front_close")) if row.get("bhavcopy_expected", True) else None
    expected_contract_volume = numeric(row.get("front_volume")) if row.get("bhavcopy_expected", True) else None
    expected_oi = numeric(row.get("front_oi")) if row.get("bhavcopy_expected", True) else None
    expected_volume = expected_contract_volume * lot if expected_contract_volume is not None and lot else None
    report.update(expected_close=expected_close, expected_contract_volume=expected_contract_volume, expected_volume=expected_volume, expected_oi=expected_oi)
    checks = [("close", actual_close, expected_close, CLOSE_TOLERANCE), ("volume", actual_volume, expected_volume, VOLUME_TOLERANCE), ("oi", actual_oi, expected_oi, OI_TOLERANCE)]
    for label, actual, expected, tolerance in checks:
        if expected is None:
            continue
        if expected == 0:
            valid = actual == 0
            report[f"{label}_relative_error"] = None if actual is None else (0.0 if actual == 0 else None)
            if not valid and "session_truncated" not in report["failures"]:
                report["failures"].append(label)
            continue
        valid = actual is not None and (actual == expected == 0 or abs(actual - expected) / abs(expected) <= tolerance)
        report[f"{label}_relative_error"] = None if actual is None else abs(actual - expected) / abs(expected)
        if not valid and "session_truncated" not in report["failures"]:
            report["failures"].append(label)
    report["ok"] = not report["failures"]
    return report


def archive_dates(root: Path) -> set[str]:
    result = set()
    for path in root.glob("*/*/banknifty_fut_*.csv"):
        match = ARCHIVE_FILE_RE.fullmatch(path.name)
        if match:
            result.add(f"{match.group(3)}-{match.group(2)}-{match.group(1)}")
    return result


def run(args: argparse.Namespace) -> int:
    load_env()
    rows = load_rows(Path(args.calendar), Path(args.gap_days), args.from_date, args.to_date)
    rows_by_date = {row["trade_date"]: row for row in rows}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["front_expiry"]].append(row)
    root = Path(args.out_root)
    before_dates = archive_dates(root)
    prior_report: dict[str, Any] = {}
    report_path = Path(args.report)
    if report_path.exists():
        try:
            prior_report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            prior_report = {}
    report: dict[str, Any] = {
        "underlying": UNDERLYING,
        "instrument_key": "groww_symbol=NSE-BANKNIFTY-{expiry}-FUT (discovered via historical contracts)",
        "coverage_before": {"file_count": len(before_dates), "first_date": min(before_dates) if before_dates else None, "last_date": max(before_dates) if before_dates else None},
        "coverage_vintage_before": prior_report.get("coverage_vintage_before", prior_report.get("coverage_before")),
        "target_rows": len(rows),
        "target_source_bhavcopy_rows": sum(row["bhavcopy_expected"] for row in rows),
        "target_source_extension_rows": sum(not row["bhavcopy_expected"] for row in rows),
        "contracts": [], "fetches": [], "fetched": [], "skipped": [], "failures": [], "warnings": [],
        "validation_results": [], "validation_failures": [],
    }
    if args.dry_run:
        report["planned_contracts"] = [{"expiry": expiry, "days": [row["trade_date"] for row in sorted(items, key=lambda item: item["trade_date"])]} for expiry, items in sorted(groups.items())]
        Path(args.report).write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
        print(f"dry-run: {len(rows)} days in {len(groups)} contracts")
        return 0

    if args.validate_only:
        raw_frames: dict[str, pd.DataFrame] = {}
        for row in rows:
            path = banknifty_file_path(row["trade_date"], root)
            if path.exists():
                raw_frames[row["trade_date"]] = pd.read_csv(path)
        lots, lot_table = derive_lots(raw_frames, rows_by_date)
        report["derived_lot_table"] = lot_table
        for row in rows:
            result = validate_file(banknifty_file_path(row["trade_date"], root), row, lots.get(row["front_expiry"]))
            report["validation_results"].append(result)
            if not result["ok"]:
                report["validation_failures"].append(result)
        report["validation_summary"] = validation_summary(report["validation_results"])
        Path(args.report).write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
        print(report["validation_summary"])
        return 1 if report["validation_failures"] else 0

    try:
        token = groww_expired.get_access_token(groww_expired.urllib_transport, os.environ.get("GROWW_API_KEY"), os.environ.get("GROWW_API_SECRET"))
    except Exception as exc:
        report["failures"].append({"stage": "authentication", "error_type": type(exc).__name__, "error": str(exc)})
        Path(args.report).write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
        print(f"authentication failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    raw_frames: dict[str, pd.DataFrame] = {}
    for expiry, target_rows in sorted(groups.items()):
        target_days = [row["trade_date"] for row in target_rows]
        try:
            contract, discovery = resolve_bank_contract(expiry, access_token=token)
            report["contracts"].append({"expiry": expiry, "groww_symbol": contract, "discovery": discovery, "trade_days": target_days})
            start, end = min(target_days), max(target_days)
            candles = groww_expired.fetch_minute_candles(contract, start, end, access_token=token, transport=groww_expired.urllib_transport)
            converted = convert_candles(candles)
            by_day = split_by_day(converted)
            report["fetches"].append({"expiry": expiry, "groww_symbol": contract, "requested_start": start, "requested_end": end, "candle_count": len(candles), "converted_rows": len(converted), "returned_dates": sorted(day.isoformat() for day in by_day)})
            for row in target_rows:
                day = date.fromisoformat(row["trade_date"])
                if day in by_day:
                    raw_frames[row["trade_date"]] = by_day[day]
        except Exception as exc:
            detail = {"stage": "contract_or_candles", "expiry": expiry, "trade_days": target_days, "error_type": type(exc).__name__, "error": str(exc)}
            if isinstance(exc, groww_expired.GrowwApiError):
                detail["http_status"] = exc.status
            report["failures"].append(detail)

    regimes, oi_observations, boundary = infer_oi_regime(raw_frames, rows_by_date)
    scaled_frames, scaling_events = apply_oi_scaling(raw_frames, regimes)
    lots, lot_table = derive_lots(raw_frames, rows_by_date)
    report["oi_regime_boundary"] = boundary
    report["oi_observations"] = oi_observations
    report["oi_scaling_events"] = scaling_events
    report["derived_lot_table"] = lot_table

    for row in rows:
        day = row["trade_date"]
        path = banknifty_file_path(day, root)
        if path.exists() and not args.overwrite:
            report["skipped"].append({"trade_date": day, "reason": "exists"})
        elif day not in scaled_frames:
            if day in KNOWN_MUHURAT_DATES:
                report["skipped"].append({"trade_date": day, "reason": "muhurat_no_regular_session"})
            else:
                report["failures"].append({"stage": "write", "trade_date": day, "error_type": "NoMarketHoursCandles", "error": "Groww returned no session candles for target date"})
        else:
            write_banknifty_day(scaled_frames[day], day, root, overwrite=args.overwrite)
            report["fetched"].append({"trade_date": day, "path": str(path)})
        result = validate_file(path, row, lots.get(row["front_expiry"]))
        report["validation_results"].append(result)
        if not result["ok"]:
            report["validation_failures"].append(result)

    after_dates = archive_dates(root)
    report["coverage_after"] = {"file_count": len(after_dates), "first_date": min(after_dates) if after_dates else None, "last_date": max(after_dates) if after_dates else None}
    report["validation_summary"] = validation_summary(report["validation_results"])
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"summary: target_days={len(rows)} files={len(report['fetched'])} skipped={len(report['skipped'])} failures={len(report['failures'])} validation_failures={len(report['validation_failures'])}")
    print(f"coverage_before={report['coverage_before']} coverage_after={report['coverage_after']}")
    print(f"oi_boundary={boundary}")
    print(f"derived_lots={lots}")
    return 1 if report["failures"] or report["validation_failures"] else 0


def validation_summary(results: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(results),
        "passed": sum(bool(result["ok"]) for result in results),
        "failed": sum(not result["ok"] for result in results),
        "skipped": sum(bool(result.get("skipped")) for result in results),
        "missing_files": sum("missing_file" in result.get("failures", []) for result in results),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calendar", default=str(DEFAULT_CALENDAR))
    parser.add_argument("--gap-days", default=str(DEFAULT_GAPS))
    parser.add_argument("--from", dest="from_date")
    parser.add_argument("--to", dest="to_date")
    parser.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
