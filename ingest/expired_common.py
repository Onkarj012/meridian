"""Shared helpers for expired-futures archive ingests."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

MARKET_OPEN = "09:15:00"
MARKET_CLOSE = "15:30:00"
# A regular session normally has 375/376 bars.  Keep the lower bound just
# below the known 346-bar truncated session so it is reported as
# ``session_truncated`` rather than as a second, less useful bar-count error.
BAR_COUNT_MIN = 345
BAR_COUNT_MAX = 380
CLOSE_TOLERANCE = 0.001
VOLUME_TOLERANCE = 0.05
OI_TOLERANCE = 0.05
IST = "Asia/Kolkata"
CSV_COLUMNS = ["date", "time", "symbol", "open", "high", "low", "close", "oi", "volume"]
KNOWN_MUHURAT_DATES = {"2024-11-01", "2025-10-21"}


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


# NSE bhavcopy reports futures volume in contracts; the minute archive (GFDL,
# Kite, Groww alike) reports it in units. Scale by the NIFTY lot size of the
# contract generation, keyed by expiry month (new lot sizes apply to newly
# listed contracts; existing ones keep theirs until expiry).
# History per brain/docs/futures-cost-schedule-research.md.
NIFTY_LOT_SIZE_BY_EXPIRY = (
    ("2021-06", 75),  # expiries through 2021-06: lot 75
    ("2024-06", 50),  # 2021-07..2024-06 expiries: lot 50
    ("2025-01", 25),  # 2024-07..2025-01 expiries: lot 25 (Jan-2025 contract retained 25)
    ("2025-12", 75),  # 2025 expiries: lot 75
    ("9999-12", 65),  # 2026+ expiries: lot 65
)


def nifty_lot_size(expiry: str) -> int:
    month = str(expiry)[:7]
    if len(month) != 7 or month[4] != "-" or not month[:4].isdigit():
        raise ValueError(f"invalid expiry for lot-size lookup: {expiry!r}")
    for last_month, lot in NIFTY_LOT_SIZE_BY_EXPIRY:
        if month <= last_month:
            return lot
    raise ValueError(f"no lot size for expiry {expiry!r}")


def validate_day_file(
    path: str | Path,
    calendar_row: Mapping[str, Any],
    *,
    oi_ratio_bounds: tuple[float, float] | None = None,
) -> dict[str, Any]:
    """Validate bar count plus bhavcopy VWAP close, volume, and OI observations."""
    path = Path(path)
    trade_date = str(calendar_row.get("trade_date", ""))[:10]
    report: dict[str, Any] = {
        "trade_date": trade_date,
        "path": str(path), "ok": True, "failures": [], "notes": [],
    }
    if trade_date in KNOWN_MUHURAT_DATES:
        report.update(ok=True, skipped=True)
        report["notes"].append("muhurat_skip")
        if path.exists():
            report["bar_count"] = len(pd.read_csv(path))
        return report
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
    volume_series = pd.to_numeric(frame["volume"], errors="coerce")
    oi_series = pd.to_numeric(frame["oi"], errors="coerce")
    close_series = pd.to_numeric(frame["close"], errors="coerce")
    last_time = str(frame["time"].iloc[-1])
    truncated = last_time < "15:25:00"
    if truncated:
        report["notes"].append("truncated_day")
        report["failures"].append("session_truncated")

    # NSE's published futures close is the last-30-minute VWAP, not the
    # final trade.  Keep the final trade in the report for diagnostics.
    last_trade_close = float(close_series.iloc[-1]) if pd.notna(close_series.iloc[-1]) else None
    close_window = frame.loc[
        (frame["time"].astype(str) >= "15:00:00")
        & (volume_series > 0)
        & close_series.notna()
    ]
    expected_close = _number(calendar_row, "front_close")
    if close_window.empty or float(volume_series.loc[close_window.index].sum()) <= 0:
        actual_close = None
    else:
        close_volume = volume_series.loc[close_window.index]
        actual_close = float((close_series.loc[close_window.index] * close_volume).sum() / close_volume.sum())
    actual_volume, expected_volume = float(volume_series.sum()), _number(calendar_row, "front_volume")
    if expected_volume is not None:
        expected_volume *= nifty_lot_size(calendar_row.get("front_expiry", ""))
    oi_rows = frame.loc[oi_series != 0]
    oi_index = oi_rows.index[-1] if not oi_rows.empty else frame.index[-1]
    actual_oi, expected_oi = float(oi_series.loc[oi_index]), _number(calendar_row, "front_oi")
    if oi_index != frame.index[-1]:
        report["notes"].append(f"oi_from_{str(frame.loc[oi_index, 'time']).replace(':', '')[:4]}_bar")
    report.update(
        last_close=last_trade_close,
        last_trade_close=last_trade_close,
        last_trade_close_observation_time=last_time,
        close_vwap=actual_close,
        close_vwap_observation_start="15:00:00",
        close_vwap_observation_end=last_time,
        volume=actual_volume,
        close_oi=actual_oi,
        oi_observation_time=str(frame.loc[oi_index, "time"]),
    )
    for label, actual, expected, tolerance in (
        ("close", actual_close, expected_close, CLOSE_TOLERANCE),
        ("volume", actual_volume, expected_volume, VOLUME_TOLERANCE),
        ("oi", actual_oi, expected_oi, OI_TOLERANCE),
    ):
        if expected is not None:
            report[f"expected_{label}"] = expected
            valid = (
                actual == 0 if expected == 0 else actual is not None and abs(actual - expected) / abs(expected) <= tolerance
            )
            if (
                not valid
                and label == "oi"
                and oi_ratio_bounds is not None
                and actual not in (None, 0)
            ):
                ratio = expected / actual
                valid = oi_ratio_bounds[0] <= ratio <= oi_ratio_bounds[1]
                if valid:
                    report["notes"].append("oi_timing_drift")
            if not valid and not truncated:
                report["failures"].append(label)
    report["ok"] = not report["failures"]
    return report
