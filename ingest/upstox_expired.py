"""Backfill expired NIFTY future minute bars from the Upstox v2 API.

``UPSTOX_ACCESS_TOKEN`` is deliberately read only by :func:`access_token_from_env`.
Upstox access tokens normally expire around 03:30 IST each day and must be
regenerated through the Upstox developer console/OAuth flow.  The expired
instruments API also requires Upstox Plus; error ``UDAPI1149`` is reported
with an actionable message.

The HTTP boundary accepts an injected callable in all API helpers.  A
transport receives ``(url, headers)`` and returns ``HttpResponse`` (or a
``(status, body)`` pair), which keeps parsing and conversion fully offline
testable.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import pandas as pd

API_BASE = "https://api.upstox.com/v2"
NIFTY_INDEX_KEY = "NSE_INDEX|Nifty 50"
MARKET_OPEN = "09:15:00"
MARKET_CLOSE = "15:30:00"
BAR_COUNT_MIN = 350
BAR_COUNT_MAX = 380
CLOSE_TOLERANCE = 0.001
VOLUME_TOLERANCE = 0.05
OI_TOLERANCE = 0.05
MAX_RETRIES = 5
REQUESTS_PER_SECOND = 2.0
IST = "Asia/Kolkata"


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: str | bytes | Mapping[str, Any] | Sequence[Any]


class UpstoxApiError(RuntimeError):
    """An Upstox response that cannot be retried successfully."""

    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


Transport = Callable[[str, Mapping[str, str]], Any]


def access_token_from_env() -> str:
    """Return the current Upstox token, or explain how to supply one."""
    token = os.environ.get("UPSTOX_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("UPSTOX_ACCESS_TOKEN is required (regenerate the daily Upstox token if needed)")
    return token


def expired_key(token: str | int, expiry: str | date | datetime) -> str:
    """Construct the expired-future key accepted by Upstox in practice."""
    expiry_day = _as_date(expiry)
    return f"NSE_FO|{_normalise_token(token)}|{expiry_day:%d-%m-%Y}"


def _as_date(value: str | date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _normalise_token(value: Any) -> str:
    text = str(value).strip()
    return text[:-2] if text.endswith(".0") and text[:-2].isdigit() else text


def urllib_transport(url: str, headers: Mapping[str, str]) -> HttpResponse:
    """Minimal live transport; kept separate from all API parsing."""
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310 - fixed API base
            return HttpResponse(response.status, response.read())
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, exc.read())
    except urllib.error.URLError as exc:
        raise UpstoxApiError(f"Upstox request failed: {exc.reason}") from exc


def _coerce_response(response: Any) -> tuple[int, Any]:
    if isinstance(response, HttpResponse):
        return response.status, response.body
    if isinstance(response, tuple) and len(response) == 2:
        return int(response[0]), response[1]
    if hasattr(response, "status") and hasattr(response, "body"):
        return int(response.status), response.body
    raise TypeError("transport must return HttpResponse or (status, body)")


def _decode_json(body: Any) -> Any:
    if isinstance(body, (dict, list)):
        return body
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    return json.loads(body)


def _error_from_payload(payload: Any, status: int) -> UpstoxApiError:
    errors = payload.get("errors", []) if isinstance(payload, dict) else []
    first = errors[0] if errors else (payload.get("error", payload) if isinstance(payload, dict) else {})
    if not isinstance(first, dict):
        first = {}
    code = str(first.get("errorCode") or first.get("code") or "") or None
    message = str(first.get("message") or payload.get("message") if isinstance(payload, dict) else "Upstox API error")
    if code == "UDAPI1149":
        message = "Upstox Plus is required for expired-instruments API access (UDAPI1149)"
    return UpstoxApiError(message, status=status, code=code)


def _get_json(
    path: str,
    *,
    access_token: str,
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    max_retries: int = MAX_RETRIES,
) -> Any:
    """GET JSON with conservative exponential retry for 429 and 5xx responses."""
    transport = transport or urllib_transport
    url = f"{API_BASE}{path}"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    for attempt in range(max_retries + 1):
        status, body = _coerce_response(transport(url, headers))
        try:
            payload = _decode_json(body)
        except (TypeError, json.JSONDecodeError) as exc:
            raise UpstoxApiError(f"Upstox returned invalid JSON (HTTP {status})", status=status) from exc
        if 200 <= status < 300 and (not isinstance(payload, dict) or payload.get("status") != "error"):
            # Keep a deliberately conservative cadence between successful calls too.
            sleep(1 / REQUESTS_PER_SECOND)
            return payload
        error = _error_from_payload(payload, status)
        if status not in (429,) and not 500 <= status < 600:
            raise error
        if attempt >= max_retries:
            raise error
        sleep((2**attempt) / REQUESTS_PER_SECOND)
    raise AssertionError("unreachable")


def _query(path: str, params: Mapping[str, str]) -> str:
    return f"{path}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"


def list_expiries(
    *, access_token: str, transport: Transport | None = None, sleep: Callable[[float], None] = time.sleep
) -> list[str]:
    """List available expired derivative expiry dates for NIFTY 50."""
    payload = _get_json(
        _query("/expired-instruments/expiries", {"instrument_key": NIFTY_INDEX_KEY}),
        access_token=access_token, transport=transport, sleep=sleep,
    )
    data = payload.get("data", [])
    if isinstance(data, dict):
        data = data.get("expiries", data.get("expiry_dates", []))
    return [str(item.get("expiry_date", item.get("expiry", "")) if isinstance(item, dict) else item) for item in data]


def get_future_contract(
    expiry: str | date,
    *, access_token: str,
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Return expired NIFTY FUT contract metadata for ``expiry``."""
    payload = _get_json(
        _query("/expired-instruments/future/contract", {
            "instrument_key": NIFTY_INDEX_KEY, "expiry_date": _as_date(expiry).isoformat(),
        }),
        access_token=access_token, transport=transport, sleep=sleep,
    )
    data = payload.get("data", {})
    if isinstance(data, list):
        if not data:
            raise UpstoxApiError(f"No expired future contract returned for {_as_date(expiry)}")
        return dict(data[0])
    if isinstance(data, dict):
        candidates = data.get("contracts") or data.get("instruments")
        if isinstance(candidates, list):
            if not candidates:
                raise UpstoxApiError(f"No expired future contract returned for {_as_date(expiry)}")
            return dict(candidates[0])
        return dict(data)
    raise UpstoxApiError("Unexpected contract response shape")


def contract_key(contract: Mapping[str, Any], token: str | int, expiry: str | date) -> str | None:
    """Return a key supplied by the contract endpoint, if it has one."""
    for name in ("expired_instrument_key", "expiredInstrumentKey", "instrument_key", "instrumentKey"):
        value = contract.get(name)
        if value:
            return str(value)
    return None


def month_chunks(from_date: str | date, to_date: str | date) -> list[tuple[date, date]]:
    """Inclusive chunks bounded by calendar months, safe for API span limits."""
    start, end = _as_date(from_date), _as_date(to_date)
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


def fetch_minute_candles(
    expired_instrument_key: str,
    from_date: str | date,
    to_date: str | date,
    *,
    access_token: str,
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[list[Any]]:
    """Fetch deduplicated minute candles, querying one calendar month at a time."""
    encoded_key = urllib.parse.quote(expired_instrument_key, safe="")
    candles: list[list[Any]] = []
    for chunk_start, chunk_end in month_chunks(from_date, to_date):
        payload = _get_json(
            f"/expired-instruments/historical-candle/{encoded_key}/1minute/{chunk_end:%Y-%m-%d}/{chunk_start:%Y-%m-%d}",
            access_token=access_token, transport=transport, sleep=sleep,
        )
        data = payload.get("data", {})
        candles.extend(data.get("candles", []) if isinstance(data, dict) else [])
    # The service does not promise ordering and adjacent queries can overlap.
    by_timestamp = {str(candle[0]): list(candle) for candle in candles if len(candle) >= 7}
    return [by_timestamp[timestamp] for timestamp in sorted(by_timestamp)]


def candles_to_frame(candles: Sequence[Sequence[Any]]) -> pd.DataFrame:
    """Convert Upstox's array candles to a sorted, IST-timezone DataFrame."""
    columns = ["timestamp", "open", "high", "low", "close", "volume", "oi"]
    frame = pd.DataFrame([list(candle[:7]) for candle in candles], columns=columns)
    if frame.empty:
        return frame
    timestamps = pd.to_datetime(frame["timestamp"], utc=True, format="mixed")
    frame["timestamp"] = timestamps.dt.tz_convert(IST)
    for column in columns[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)


def candles_to_futures_csv(candles: Sequence[Sequence[Any]] | pd.DataFrame) -> pd.DataFrame:
    """Convert candles to the archive's exact NIFTY-I per-minute CSV schema."""
    frame = candles if isinstance(candles, pd.DataFrame) else candles_to_frame(candles)
    columns = ["date", "time", "symbol", "open", "high", "low", "close", "oi", "volume"]
    if frame.empty:
        return pd.DataFrame(columns=columns)
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
    """Split an archive-shaped frame into one frame for each trade date."""
    if csv_frame.empty:
        return {}
    return {day: group.reset_index(drop=True) for day, group in csv_frame.groupby(pd.to_datetime(csv_frame["date"]).dt.date)}


def minute_file_path(day: str | date, out_root: str | Path) -> Path:
    trade_day = _as_date(day)
    return Path(out_root) / str(trade_day.year) / str(trade_day.month) / f"nifty_fut_{trade_day:%d_%m_%Y}.csv"


def write_day_csv(csv_frame: pd.DataFrame, day: str | date, out_root: str | Path, *, overwrite: bool = False) -> Path | None:
    """Write a single archive day, returning ``None`` when retained unchanged."""
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
    """Validate bar count and available bhavcopy close/volume/OI observations."""
    path = Path(path)
    report: dict[str, Any] = {"trade_date": str(calendar_row.get("trade_date", "")), "path": str(path), "ok": True, "failures": []}
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
            if expected == 0:
                valid = actual == 0
            else:
                valid = abs(actual - expected) / abs(expected) <= tolerance
            if not valid:
                report["failures"].append(label)
    report["ok"] = not report["failures"]
    return report
