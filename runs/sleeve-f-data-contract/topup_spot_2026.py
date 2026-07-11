#!/usr/bin/env python3
"""Top up Groww NIFTY spot minute data for 2026-06-17 through 2026-07-10."""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from ingest import groww_expired
from ingest.envfile import load_env


ARCHIVE_ROOT = Path(os.environ.get("NIFTY_SPOT_ARCHIVE_ROOT", "/Users/onkarj012/Projects/market/intranet_optinet/data/option_data/nifty_data/nifty_spot"))
MINUTE_FILE = Path(os.environ.get("NIFTY_SPOT_MINUTE_FILE", "/Users/onkarj012/Projects/market/intranet_optinet/data/nifty_intraday/NIFTY 50_minute.csv"))
BACKUP_FILE = Path(str(MINUTE_FILE) + ".bak-20260711")
REPORT_FILE = PROJECT_ROOT / "runs/sleeve-f-data-contract/topup_spot_2026_report.json"
START = date(2026, 6, 17)
END = date(2026, 7, 10)
CHECK_DAY = date(2026, 6, 16)
SYMBOL = "NSE-NIFTY"
OUTPUT_COLUMNS = ["date", "time", "symbol", "open", "high", "low", "close"]
MINUTE_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


def write_report(report: dict[str, Any]) -> None:
    REPORT_FILE.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")


def fetch_candles(symbol: str, start: date, end: date, *, token: str, interval: str = "1minute") -> list[list[Any]]:
    result: list[list[Any]] = []
    for chunk_start, chunk_end in groww_expired.day_chunks(start, end):
        params = {
            "exchange": "NSE",
            "segment": "CASH",
            "groww_symbol": symbol,
            "start_time": f"{chunk_start:%Y-%m-%d} 00:00:00" if interval == "1day" else f"{chunk_start:%Y-%m-%d} 09:15:00",
            "end_time": f"{chunk_end:%Y-%m-%d} 23:59:59" if interval == "1day" else f"{chunk_end:%Y-%m-%d} 15:30:00",
            "candle_interval": interval,
        }
        response = groww_expired._request_json(
            groww_expired._query(groww_expired.HISTORICAL_CANDLES_PATH, params),
            access_token=token,
            transport=groww_expired.urllib_transport,
        )
        result.extend(groww_expired._validate_candles(response["payload"]["candles"]))
    return list({str(row[0]): row for row in result}.values())


def minute_frame(candles: list[list[Any]]) -> pd.DataFrame:
    frame = groww_expired.candles_to_frame(candles)
    if frame.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS + ["timestamp"])
    frame = frame[(frame["timestamp"].dt.strftime("%H:%M:%S") >= "09:15:00") & (frame["timestamp"].dt.strftime("%H:%M:%S") <= "15:30:00")].copy()
    frame["trade_date"] = frame["timestamp"].dt.date
    frame = frame[frame["trade_date"].between(START, END) | frame["trade_date"].eq(CHECK_DAY)].copy()
    for column in ["open", "high", "low", "close"]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    return frame.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)


def archive_frame(day: date, frame: pd.DataFrame) -> pd.DataFrame:
    rows = frame[frame["trade_date"].eq(day)].copy()
    return pd.DataFrame(
        {
            "date": rows["timestamp"].dt.strftime("%Y-%m-%d"),
            "time": rows["timestamp"].dt.strftime("%H:%M:%S"),
            "symbol": "NIFTY",
            "open": rows["open"].round(2),
            "high": rows["high"].round(2),
            "low": rows["low"].round(2),
            "close": rows["close"].round(2),
        },
        columns=OUTPUT_COLUMNS,
    ).reset_index(drop=True)


def provenance(frame: pd.DataFrame, vintage: pd.DataFrame) -> dict[str, Any]:
    source = frame[frame["trade_date"].eq(CHECK_DAY)][["timestamp", "close"]].copy()
    source["timestamp"] = source["timestamp"].dt.tz_localize(None)
    old = vintage.copy()
    old["timestamp"] = pd.to_datetime(old["date"], errors="raise")
    old = old[old["timestamp"].dt.date.eq(CHECK_DAY)][["timestamp", "close"]]
    merged = source.merge(old, on="timestamp", how="outer", suffixes=("_groww", "_vintage"), indicator=True)
    diff = (merged["close_groww"] - merged["close_vintage"]).abs()
    denominator = merged["close_vintage"].abs().where(merged["close_vintage"].abs().ne(0))
    relative = diff / denominator
    max_relative = float(relative.max()) if relative.notna().any() else float("inf")
    return {
        "trade_date": CHECK_DAY.isoformat(),
        "source_rows": len(source),
        "vintage_rows": len(old),
        "matching_timestamps": int(merged["_merge"].eq("both").sum()),
        "source_only_timestamps": int(merged["_merge"].eq("left_only").sum()),
        "vintage_only_timestamps": int(merged["_merge"].eq("right_only").sum()),
        "max_abs_close_diff": float(diff.max()) if diff.notna().any() else None,
        "max_relative_close_diff": max_relative,
        "drift_over_threshold": bool(max_relative > 0.0005),
    }


def load_minute_file() -> pd.DataFrame:
    raw_header = MINUTE_FILE.read_text(encoding="utf-8").splitlines()[0]
    if raw_header != ",".join(MINUTE_COLUMNS):
        raise ValueError(f"unexpected NIFTY minute header: {raw_header!r}")
    frame = pd.read_csv(MINUTE_FILE)
    if list(frame.columns) != MINUTE_COLUMNS:
        raise ValueError(f"unexpected NIFTY minute columns: {list(frame.columns)!r}")
    if pd.to_numeric(frame["volume"], errors="coerce").isna().any() or not pd.to_numeric(frame["volume"], errors="coerce").eq(0).all():
        raise ValueError("NIFTY minute volume is not uniformly zero")
    frame["timestamp"] = pd.to_datetime(frame["date"], errors="raise")
    if frame["timestamp"].duplicated().any():
        raise ValueError("NIFTY minute file already contains duplicate timestamps")
    return frame


def spot_path(day: date) -> Path:
    return ARCHIVE_ROOT / str(day.year) / str(day.month) / f"nifty_spot{day:%d_%m_%Y}.csv"


def main() -> int:
    load_env()
    report: dict[str, Any] = {
        "source": {"provider": "Groww", "endpoint": groww_expired.HISTORICAL_CANDLES_PATH, "symbol": SYMBOL, "segment": "CASH", "interval": "1minute"},
        "range": {"start": START.isoformat(), "end": END.isoformat()},
        "backup_path": str(BACKUP_FILE),
        "days_appended": [],
        "archive_files_written": [],
        "archive_days_skipped": [],
        "no_source_data_days": [],
    }
    try:
        vintage = load_minute_file()
        token = groww_expired.get_access_token(
            groww_expired.urllib_transport,
            os.environ.get("GROWW_API_KEY"),
            os.environ.get("GROWW_API_SECRET"),
        )
        check_frame = minute_frame(fetch_candles(SYMBOL, CHECK_DAY, CHECK_DAY, token=token))
        check = provenance(pd.concat([check_frame], ignore_index=True), vintage)
        report["provenance_check"] = check
        if check["drift_over_threshold"]:
            report["blocked"] = True
            report["failure"] = "2026-06-16 Groww close drift exceeded 0.05%; no target files or CSV rows written."
            write_report(report)
            print(json.dumps(report, indent=2))
            return 2

        target_frame = minute_frame(fetch_candles(SYMBOL, START, END, token=token))
        old_last = vintage["timestamp"].max()
        report["existing_last_timestamp"] = str(old_last)
        report["source_rows_in_target_range"] = len(target_frame)
        if old_last.date() < CHECK_DAY or old_last > pd.Timestamp(f"{END} 15:30:00"):
            raise ValueError(f"unexpected existing NIFTY last timestamp: {old_last}")

        missing_days: list[date] = []
        daily_frames: dict[date, pd.DataFrame] = {}
        for day in sorted(set(target_frame["trade_date"].tolist())):
            daily = archive_frame(day, target_frame)
            if daily.empty:
                continue
            daily_frames[day] = daily
            path = spot_path(day)
            if path.exists():
                report["archive_days_skipped"].append(day.isoformat())
            else:
                missing_days.append(day)
        report["missing_archive_days"] = [day.isoformat() for day in missing_days]

        new_rows = []
        existing_timestamps = set(vintage["timestamp"])
        for day in sorted(daily_frames):
            daily = daily_frames[day].copy()
            daily["timestamp"] = pd.to_datetime(daily["date"] + " " + daily["time"])
            daily = daily[(daily["timestamp"] > old_last) & ~daily["timestamp"].isin(existing_timestamps)]
            if not daily.empty:
                new_rows.append(
                    pd.DataFrame(
                        {
                            "date": daily["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S"),
                            "open": daily["open"],
                            "high": daily["high"],
                            "low": daily["low"],
                            "close": daily["close"],
                            "volume": 0,
                        },
                        columns=MINUTE_COLUMNS,
                    )
                )
        append_frame = pd.concat(new_rows, ignore_index=True) if new_rows else pd.DataFrame(columns=MINUTE_COLUMNS)
        append_frame = append_frame.sort_values("date").reset_index(drop=True)
        if not append_frame.empty:
            if BACKUP_FILE.exists():
                report["backup_preexisting"] = True
            else:
                shutil.copy2(MINUTE_FILE, BACKUP_FILE)
                report["backup_preexisting"] = False
            append_frame.to_csv(MINUTE_FILE, mode="a", header=False, index=False)
            report["days_appended"] = sorted(pd.to_datetime(append_frame["date"]).dt.date.astype(str).unique().tolist())
            report["appended_rows"] = len(append_frame)
        else:
            report["appended_rows"] = 0

        for day in missing_days:
            path = spot_path(day)
            path.parent.mkdir(parents=True, exist_ok=True)
            daily_frames[day].to_csv(path, index=False)
            report["archive_files_written"].append({"trade_date": day.isoformat(), "path": str(path), "rows": len(daily_frames[day])})
        report["blocked"] = False
        write_report(report)
        print(json.dumps(report, indent=2))
        return 0
    except Exception as exc:
        report["blocked"] = True
        report["failure"] = f"{type(exc).__name__}: {exc}"
        write_report(report)
        print(json.dumps(report, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
