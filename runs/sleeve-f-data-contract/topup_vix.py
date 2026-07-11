#!/usr/bin/env python3
"""Top up the India VIX daily archive, preferring Groww index candles."""
from __future__ import annotations

import json
import os
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from ingest import groww_expired
from ingest.envfile import load_env


VIX_FILE = Path(os.environ.get("VIX_FILE", "/Users/onkarj012/Projects/market/intranet_optinet/data/nifty_intraday/INDIA VIX_day.csv"))
BACKUP_FILE = Path(str(VIX_FILE) + ".bak-20260711")
REPORT_FILE = PROJECT_ROOT / "runs/sleeve-f-data-contract/topup_vix_report.json"
START = date(2026, 6, 17)
END = date(2026, 7, 10)
CHECK_DAY = date(2026, 6, 16)
COLUMNS = ["date", "open", "high", "low", "close", "volume"]


def write_report(report: dict[str, Any]) -> None:
    REPORT_FILE.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")


def load_vix_file() -> pd.DataFrame:
    raw_header = VIX_FILE.read_text(encoding="utf-8").splitlines()[0]
    if raw_header != ",".join(COLUMNS):
        raise ValueError(f"unexpected India VIX header: {raw_header!r}")
    frame = pd.read_csv(VIX_FILE)
    if list(frame.columns) != COLUMNS:
        raise ValueError(f"unexpected India VIX columns: {list(frame.columns)!r}")
    frame["timestamp"] = pd.to_datetime(frame["date"], format="mixed", errors="raise").dt.normalize()
    if frame["date"].duplicated().any():
        raise ValueError("India VIX file already contains duplicate raw date strings")
    volume = pd.to_numeric(frame["volume"], errors="coerce")
    if volume.isna().any() or not volume.eq(0).all():
        raise ValueError("India VIX volume is not uniformly zero")
    return frame


def fetch_groww(symbol: str, *, token: str) -> list[list[Any]]:
    params = {
        "exchange": "NSE",
        "segment": "CASH",
        "groww_symbol": symbol,
        "start_time": f"{CHECK_DAY:%Y-%m-%d} 00:00:00",
        "end_time": f"{END:%Y-%m-%d} 23:59:59",
        "candle_interval": "1day",
    }
    response = groww_expired._request_json(
        groww_expired._query(groww_expired.HISTORICAL_CANDLES_PATH, params),
        access_token=token,
        transport=groww_expired.urllib_transport,
    )
    return groww_expired._validate_candles(response["payload"]["candles"])


def candles_to_daily(candles: list[list[Any]]) -> pd.DataFrame:
    frame = groww_expired.candles_to_frame(candles)
    if frame.empty:
        return pd.DataFrame(columns=COLUMNS + ["timestamp"])
    frame["timestamp"] = frame["timestamp"].dt.normalize().dt.tz_localize(None)
    frame = frame[frame["timestamp"].dt.date.between(CHECK_DAY, END)].copy()
    frame = frame.drop_duplicates("timestamp").sort_values("timestamp")
    return frame[["timestamp", "open", "high", "low", "close"]].reset_index(drop=True)


def provenance(source: pd.DataFrame, vintage: pd.DataFrame) -> dict[str, Any]:
    source_close = float(source.loc[source["timestamp"].eq(pd.Timestamp(CHECK_DAY)), "close"].iloc[0])
    old_close = float(vintage.loc[vintage["timestamp"].eq(pd.Timestamp(CHECK_DAY)), "close"].iloc[0])
    diff = abs(source_close - old_close)
    relative = diff / abs(old_close) if old_close else float("inf")
    return {
        "trade_date": CHECK_DAY.isoformat(),
        "source_close": source_close,
        "vintage_close": old_close,
        "abs_close_diff": diff,
        "relative_close_diff": relative,
        "drift_over_threshold": bool(relative > 0.0005),
    }


def fetch_nse_archive() -> tuple[pd.DataFrame, list[str]]:
    failures: list[str] = []
    url = "https://www.nseindia.com/api/historical/indicesHistory?" + urllib.parse.urlencode(
        {"indexType": "INDIA VIX", "from": CHECK_DAY.strftime("%d-%m-%Y"), "to": END.strftime("%d-%m-%Y")}
    )
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json, text/plain, */*",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36",
            "Referer": "https://www.nseindia.com/",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise ValueError(f"unexpected NSE response shape: {payload!r}")
        result = []
        for row in rows:
            timestamp = pd.to_datetime(row.get("CH_TIMESTAMP") or row.get("EOD_TIMESTAMP") or row.get("TIMESTAMP"), errors="coerce")
            if pd.isna(timestamp):
                continue
            result.append({
                "timestamp": timestamp.normalize(),
                "open": row.get("CH_OPENING_PRICE") or row.get("OPEN_INDEX_VAL"),
                "high": row.get("CH_TRADE_HIGH_PRICE") or row.get("HIGH_INDEX_VAL"),
                "low": row.get("CH_TRADE_LOW_PRICE") or row.get("LOW_INDEX_VAL"),
                "close": row.get("CH_CLOSING_PRICE") or row.get("CLOSE_INDEX_VAL"),
            })
        frame = pd.DataFrame(result)
        if frame.empty:
            raise ValueError("NSE archive returned no India VIX rows")
        return frame.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True), failures
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, ValueError, json.JSONDecodeError) as exc:
        failures.append(f"NSE indices archive: {type(exc).__name__}: {exc}")
        return pd.DataFrame(), failures


def fetch_yfinance() -> pd.DataFrame:
    try:
        import yfinance as yf  # type: ignore
    except ImportError as exc:
        raise RuntimeError("yfinance is not installed in .venv") from exc
    raw = yf.download("^INDIAVIX", start=CHECK_DAY.isoformat(), end="2026-07-11", progress=False, auto_adjust=False)
    if raw.empty:
        raise RuntimeError("yfinance returned no ^INDIAVIX rows")
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = [column[0] for column in raw.columns]
    raw = raw.reset_index()
    raw.columns = [str(column).lower().replace(" ", "_") for column in raw.columns]
    date_column = "date" if "date" in raw else "datetime"
    return pd.DataFrame({
        "timestamp": pd.to_datetime(raw[date_column], errors="raise").dt.normalize(),
        "open": raw["open"], "high": raw["high"], "low": raw["low"], "close": raw["close"],
    })


def main() -> int:
    load_env()
    report: dict[str, Any] = {
        "range": {"start": START.isoformat(), "end": END.isoformat()},
        "backup_path": str(BACKUP_FILE),
        "days_appended": [],
        "attempts": [],
    }
    try:
        vintage = load_vix_file()
        token = groww_expired.get_access_token(
            groww_expired.urllib_transport,
            os.environ.get("GROWW_API_KEY"),
            os.environ.get("GROWW_API_SECRET"),
        )
        source = pd.DataFrame()
        for symbol, label in [("NSE_INDEX|India VIX", "Groww Upstox-style index key"), ("NSE-INDIAVIX", "Groww instrument symbol")]:
            try:
                source = candles_to_daily(fetch_groww(symbol, token=token))
                if not source.empty:
                    report["attempts"].append({"source": label, "symbol": symbol, "status": "success", "rows": len(source)})
                    report["source"] = {"provider": "Groww", "symbol": symbol, "endpoint": groww_expired.HISTORICAL_CANDLES_PATH, "interval": "1day"}
                    break
                report["attempts"].append({"source": label, "symbol": symbol, "status": "empty", "rows": 0})
            except Exception as exc:
                report["attempts"].append({"source": label, "symbol": symbol, "status": "failed", "failure": f"{type(exc).__name__}: {exc}"})
        if source.empty:
            source, failures = fetch_nse_archive()
            report["attempts"].extend({"source": "NSE indices archive", "status": "failed", "failure": failure} for failure in failures)
            if not source.empty:
                report["source"] = {"provider": "NSE indices archive"}
        if source.empty:
            try:
                source = fetch_yfinance()
                report["source"] = {"provider": "yfinance", "ticker": "^INDIAVIX"}
            except Exception as exc:
                report["attempts"].append({"source": "yfinance", "ticker": "^INDIAVIX", "status": "failed", "failure": f"{type(exc).__name__}: {exc}"})
        if source.empty:
            raise RuntimeError("no India VIX source produced rows; see attempts")

        source["timestamp"] = pd.to_datetime(source["timestamp"], errors="raise").dt.normalize()
        source = source[source["timestamp"].dt.date.between(CHECK_DAY, END)].copy()
        check = provenance(source, vintage)
        report["provenance_check"] = check
        if check["drift_over_threshold"]:
            report["blocked"] = True
            report["failure"] = "2026-06-16 India VIX close drift exceeded 0.05%; file left untouched."
            write_report(report)
            print(json.dumps(report, indent=2))
            return 2

        old_last = vintage["timestamp"].max()
        report["existing_last_date"] = str(old_last.date())
        new = source[source["timestamp"] > old_last].copy()
        existing_dates = set(vintage["timestamp"])
        new = new[~new["timestamp"].isin(existing_dates)].copy()
        new = new.sort_values("timestamp")
        append = pd.DataFrame({
            "date": new["timestamp"].dt.strftime("%Y-%m-%d"),
            "open": pd.to_numeric(new["open"], errors="raise"),
            "high": pd.to_numeric(new["high"], errors="raise"),
            "low": pd.to_numeric(new["low"], errors="raise"),
            "close": pd.to_numeric(new["close"], errors="raise"),
            "volume": 0,
        }, columns=COLUMNS)
        if not append.empty:
            if BACKUP_FILE.exists():
                report["backup_preexisting"] = True
            else:
                shutil.copy2(VIX_FILE, BACKUP_FILE)
                report["backup_preexisting"] = False
            append.to_csv(VIX_FILE, mode="a", header=False, index=False)
            report["days_appended"] = append["date"].tolist()
        report["appended_rows"] = len(append)
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
