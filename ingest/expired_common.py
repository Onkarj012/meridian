"""Shared helpers for expired-futures archive ingests."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

MARKET_OPEN = "09:15:00"
MARKET_CLOSE = "15:30:00"
BAR_COUNT_MIN = 350
BAR_COUNT_MAX = 380
CLOSE_TOLERANCE = 0.001
VOLUME_TOLERANCE = 0.05
OI_TOLERANCE = 0.05
IST = "Asia/Kolkata"
CSV_COLUMNS = ["date", "time", "symbol", "open", "high", "low", "close", "oi", "volume"]


def as_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def normalise_token(value: Any) -> str:
    text = str(value).strip()
    return text[:-2] if text.endswith(".0") and text[:-2].isdigit() else text


def month_chunks(from_date: str | date, to_date: str | date) -> list[tuple[date, date]]:
    """Return inclusive calendar-month chunks."""
    start, end = as_date(from_date), as_date(to_date)
    if end < start:
        raise ValueError("to_date must be on or after from_date")
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        next_month = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
        chunk_end = min(end, next_month - timedelta(days=1))
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def _parse_timestamp(value: Any, naive_timezone: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize(naive_timezone)
    else:
        timestamp = timestamp.tz_convert(IST)
    return timestamp


def candles_to_frame(
    candles: Sequence[Sequence[Any]], *, naive_timezone: str = "UTC"
) -> pd.DataFrame:
    """Convert array candles to the common sorted, IST-aware frame shape."""
    columns = ["timestamp", "open", "high", "low", "close", "volume", "oi"]
    rows = [list(candle[:7]) for candle in candles]
    frame = pd.DataFrame(rows, columns=columns)
    if frame.empty:
        return frame
    frame["timestamp"] = [_parse_timestamp(value, naive_timezone) for value in frame["timestamp"]]
    for column in columns[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)


def candles_to_futures_csv(candles: Sequence[Sequence[Any]] | pd.DataFrame) -> pd.DataFrame:
    """Convert a candle frame into the exact NIFTY-I archive schema."""
    frame = candles if isinstance(candles, pd.DataFrame) else candles_to_frame(candles)
    if frame.empty:
        return pd.DataFrame(columns=CSV_COLUMNS)
    work = frame.copy()
    if "timestamp" not in work:
        raise ValueError("candle frame must contain timestamp")
    times = work["timestamp"].dt.strftime("%H:%M:%S")
    work = work[(times >= MARKET_OPEN) & (times <= MARKET_CLOSE)].copy()
    return pd.DataFrame({
        "date": work["timestamp"].dt.strftime("%Y-%m-%d"),
        "time": work["timestamp"].dt.strftime("%H:%M:%S"),
        "symbol": "NIFTY-I",
        "open": work["open"].round(4), "high": work["high"].round(4),
        "low": work["low"].round(4), "close": work["close"].round(4),
        "oi": work["oi"].fillna(0).astype(int), "volume": work["volume"].fillna(0).astype(int),
    }).reset_index(drop=True)


def split_by_day(csv_frame: pd.DataFrame) -> dict[date, pd.DataFrame]:
    if csv_frame.empty:
        return {}
    return {
        day: group.reset_index(drop=True)
        for day, group in csv_frame.groupby(pd.to_datetime(csv_frame["date"]).dt.date)
    }


def minute_file_path(day: str | date, out_root: str | Path) -> Path:
    trade_day = as_date(day)
    return Path(out_root) / str(trade_day.year) / str(trade_day.month) / f"nifty_fut_{trade_day:%d_%m_%Y}.csv"


def write_day_csv(
    csv_frame: pd.DataFrame, day: str | date, out_root: str | Path, *, overwrite: bool = False
) -> Path | None:
    path = minute_file_path(day, out_root)
    if path.exists() and not overwrite:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    csv_frame.to_csv(path, index=False)
    return path


def _number(row: Mapping[str, Any], field: str) -> float | None:
    value = row.get(field)
    if value is None or pd.isna(value) or str(value).strip() == "":
        return None
    return float(value)


def validate_day_file(path: str | Path, calendar_row: Mapping[str, Any]) -> dict[str, Any]:
    """Validate bar count plus bhavcopy close, volume, and OI observations."""
    path = Path(path)
    report: dict[str, Any] = {
        "trade_date": str(calendar_row.get("trade_date", "")),
        "path": str(path), "ok": True, "failures": [],
    }
    if not path.exists():
        report.update(ok=False, failures=["missing_file"])
        return report
    frame = pd.read_csv(path)
    report["bar_count"] = len(frame)
    if not BAR_COUNT_MIN <= len(frame) <= BAR_COUNT_MAX:
        report["failures"].append("bar_count")
    if frame.empty:
        report["failures"].extend(["close", "volume", "oi"])
        report["ok"] = False
        return report
    actual_close, expected_close = float(frame.iloc[-1]["close"]), _number(calendar_row, "front_close")
    actual_volume, expected_volume = float(frame["volume"].sum()), _number(calendar_row, "front_volume")
    actual_oi, expected_oi = float(frame.iloc[-1]["oi"]), _number(calendar_row, "front_oi")
    report.update(last_close=actual_close, volume=actual_volume, close_oi=actual_oi)
    for label, actual, expected, tolerance in (
        ("close", actual_close, expected_close, CLOSE_TOLERANCE),
        ("volume", actual_volume, expected_volume, VOLUME_TOLERANCE),
        ("oi", actual_oi, expected_oi, OI_TOLERANCE),
    ):
        if expected is not None:
            report[f"expected_{label}"] = expected
            valid = actual == 0 if expected == 0 else abs(actual - expected) / abs(expected) <= tolerance
            if not valid:
                report["failures"].append(label)
    report["ok"] = not report["failures"]
    return report
