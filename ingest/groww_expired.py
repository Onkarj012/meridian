"""Backfill expired NIFTY future minute bars from Groww Backtesting APIs."""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from typing import Any, Callable, Mapping, Sequence

import pandas as pd

from .expired_common import (
    IST,
    as_date,
    candles_to_frame as _candles_to_frame,
    candles_to_futures_csv as _candles_to_futures_csv,
    minute_file_path,
    split_by_day,
    validate_day_file,
    write_day_csv,
)

API_BASE = "https://api.groww.in"
TOKEN_PATH = "/v1/token/api/access"
HISTORICAL_EXPIRIES_PATH = "/v1/historical/expiries"
HISTORICAL_CONTRACTS_PATH = "/v1/historical/contracts"
HISTORICAL_CANDLES_PATH = "/v1/historical/candles"
EXCHANGE = "NSE"
UNDERLYING = "NIFTY"
SEGMENT = "FNO"
CANDLE_INTERVAL = "1minute"
MAX_RETRIES = 5
REQUESTS_PER_SECOND = 2.0

# Offline forensics measured the Groww OI unit boundary at 2025-01-01:
# runs/sleeve-f-data-contract/backfill_forensics.md.  The calendar ratio is
# authoritative when present; this date is only the no-calendar fallback.
GROWW_OI_X100_START_DATE = date(2025, 1, 1)
GROWW_OI_NEAR_ONE_RATIO_MIN = 0.5
GROWW_OI_NEAR_ONE_RATIO_MAX = 2.0
GROWW_OI_X100_RATIO_MIN = 10.0
# After unit normalization, the observed expiry/timing tails span this
# measured bhavcopy/file ratio envelope; retain them as report notes.
GROWW_OI_VALIDATION_RATIO_BOUNDS = (0.2, 2.0)


class GrowwApiError(RuntimeError):
    """A Groww response that is invalid, unsuccessful, or not retryable."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


class HttpResponse:
    def __init__(self, status: int, body: str | bytes | Mapping[str, Any] | Sequence[Any]):
        self.status = status
        self.body = body


Transport = Callable[..., Any]


def approval_checksum(api_secret: str, timestamp: str | int) -> str:
    """Return SHA-256 hex(api_secret + epoch-seconds), as required by Groww."""
    return hashlib.sha256(f"{api_secret}{timestamp}".encode("utf-8")).hexdigest()


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


def urllib_transport(
    url: str,
    headers: Mapping[str, str],
    method: str = "GET",
    body: str | bytes | None = None,
) -> HttpResponse:
    """Minimal stdlib transport, also usable by the token POST."""
    data = body.encode("utf-8") if isinstance(body, str) else body
    request = urllib.request.Request(url, headers=dict(headers), data=data, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310 - fixed API base
            return HttpResponse(response.status, response.read())
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, exc.read())
    except urllib.error.URLError as exc:
        raise GrowwApiError(f"Groww request failed: {exc.reason}") from exc


def _invoke_transport(transport: Transport, url: str, headers: Mapping[str, str], method: str, body: str | None) -> Any:
    """Support the Upstox two-argument GET mock and request-aware POST mocks."""
    try:
        signature = inspect.signature(transport)
        signature.bind(url, headers, method, body)
    except (TypeError, ValueError):
        return transport(url, headers)
    return transport(url, headers, method, body)


def _error_message(payload: Any, status: int) -> str:
    if isinstance(payload, dict):
        for key in ("message", "error", "errors"):
            value = payload.get(key)
            if value:
                return f"Groww API error (HTTP {status}): {value}"
    return f"Groww API error (HTTP {status}): {payload!r}"


def _request_json(
    path: str,
    *,
    access_token: str | None = None,
    transport: Transport | None = None,
    method: str = "GET",
    body: Mapping[str, Any] | None = None,
    extra_headers: Mapping[str, str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    max_retries: int = MAX_RETRIES,
) -> Any:
    transport = transport or urllib_transport
    headers = {"Accept": "application/json"}
    if access_token is not None:
        headers.update({"Authorization": f"Bearer {access_token}", "X-API-VERSION": "1.0"})
    if extra_headers:
        headers.update(extra_headers)
    if method == "POST":
        headers["Content-Type"] = "application/json"
    encoded_body = json.dumps(body, separators=(",", ":")) if body is not None else None
    url = path if path.startswith("http") else f"{API_BASE}{path}"
    for attempt in range(max_retries + 1):
        status, raw = _coerce_response(_invoke_transport(transport, url, headers, method, encoded_body))
        try:
            payload = _decode_json(raw)
        except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GrowwApiError(f"Groww returned invalid JSON (HTTP {status})", status=status) from exc
        success_status = isinstance(payload, dict) and str(payload.get("status", "")).upper() in {"SUCCESS", "OK"}
        if 200 <= status < 300 and (not isinstance(payload, dict) or not payload.get("status") or success_status):
            sleep(1 / REQUESTS_PER_SECOND)
            return payload
        if status not in {429} and not 500 <= status < 600:
            raise GrowwApiError(_error_message(payload, status), status=status)
        if attempt >= max_retries:
            raise GrowwApiError(_error_message(payload, status), status=status)
        sleep((2**attempt) / REQUESTS_PER_SECOND)
    raise AssertionError("unreachable")


def get_access_token(
    transport: Transport,
    api_key: str | None,
    api_secret: str | None,
    *,
    timestamp: str | int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Use a ready env token or exchange an approval key/secret for one."""
    env_token = os.environ.get("GROWW_ACCESS_TOKEN")
    if env_token:
        return env_token
    if not api_key or not api_secret:
        raise RuntimeError("GROWW_API_KEY and GROWW_API_SECRET are required when GROWW_ACCESS_TOKEN is unset")
    epoch = str(int(time.time()) if timestamp is None else timestamp)
    payload = {"key_type": "approval", "checksum": approval_checksum(api_secret, epoch), "timestamp": epoch}
    response = _request_json(
        TOKEN_PATH,
        transport=transport,
        method="POST",
        body=payload,
        extra_headers={"Authorization": f"Bearer {api_key}"},
        sleep=sleep,
    )
    if not isinstance(response, dict) or not isinstance(response.get("token"), str) or not response["token"]:
        raise GrowwApiError("Groww token response shape differs from the documented {token: ...} response")
    return response["token"]


def _query(path: str, params: Mapping[str, Any]) -> str:
    return f"{path}?{urllib.parse.urlencode(params, quote_via=urllib.parse.quote)}"


def list_expiries(
    *,
    access_token: str,
    year: int | None = None,
    month: int | None = None,
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[str]:
    params: dict[str, Any] = {"exchange": EXCHANGE, "underlying_symbol": UNDERLYING}
    if year is not None:
        params["year"] = year
    if month is not None:
        params["month"] = month
    response = _request_json(_query(HISTORICAL_EXPIRIES_PATH, params), access_token=access_token, transport=transport, sleep=sleep)
    try:
        expiries = response["payload"]["expiries"]
        if not isinstance(expiries, list) or not all(isinstance(value, str) for value in expiries):
            raise TypeError
    except (KeyError, TypeError) as exc:
        raise GrowwApiError("Groww Get Expiries response differs from documented payload.expiries list") from exc
    return expiries


def list_contracts(
    expiry: str | date,
    *,
    access_token: str,
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[str]:
    response = _request_json(
        _query(HISTORICAL_CONTRACTS_PATH, {
            "exchange": EXCHANGE, "underlying_symbol": UNDERLYING, "expiry_date": as_date(expiry).isoformat(),
        }),
        access_token=access_token, transport=transport, sleep=sleep,
    )
    try:
        contracts = response["payload"]["contracts"]
        if not isinstance(contracts, list) or not all(isinstance(value, str) for value in contracts):
            raise TypeError
    except (KeyError, TypeError) as exc:
        raise GrowwApiError("Groww Get Contracts response differs from documented payload.contracts string list") from exc
    return contracts


def get_future_contract(
    expiry: str | date,
    *,
    access_token: str,
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Resolve the NIFTY futures groww_symbol from Get Contracts."""
    contracts = list_contracts(expiry, access_token=access_token, transport=transport, sleep=sleep)
    futures = [value for value in contracts if value.endswith("-FUT")]
    if not futures:
        raise GrowwApiError(
            f"Groww returned no NIFTY futures contract ending -FUT for expiry {as_date(expiry):%Y-%m-%d}; "
            "verify the live Get Contracts response shape/availability"
        )
    return futures[0]


def resolve_future_contract(
    expiry: str | date,
    *,
    access_token: str,
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """Run the documented Get Expiries → Get Contracts discovery chain."""
    expiry_day = as_date(expiry)
    expiries = list_expiries(
        access_token=access_token, year=expiry_day.year, month=expiry_day.month,
        transport=transport, sleep=sleep,
    )
    if expiry_day.isoformat() not in expiries:
        raise GrowwApiError(
            f"Groww Get Expiries did not return requested expiry {expiry_day:%Y-%m-%d}; "
            "verify historical expiry availability and the live response"
        )
    return get_future_contract(expiry_day, access_token=access_token, transport=transport, sleep=sleep)


def day_chunks(from_date: str | date, to_date: str | date) -> list[tuple[date, date]]:
    """Return inclusive chunks of at most 30 calendar days."""
    start, end = as_date(from_date), as_date(to_date)
    if end < start:
        raise ValueError("to_date must be on or after from_date")
    chunks = []
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=29))
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def _validate_candles(candles: Any) -> list[list[Any]]:
    if not isinstance(candles, list):
        raise GrowwApiError("Groww candle response differs from documented payload.candles list")
    result = []
    for candle in candles:
        if not isinstance(candle, list) or len(candle) < 7:
            raise GrowwApiError("Groww candle row differs from documented [timestamp, open, high, low, close, volume, oi] shape")
        result.append(list(candle[:7]))
    return result


def fetch_minute_candles(
    groww_symbol: str,
    from_date: str | date,
    to_date: str | date,
    *,
    access_token: str,
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[list[Any]]:
    """Fetch deduplicated 1-minute candles using the documented 30-day cap."""
    candles: list[list[Any]] = []
    for chunk_start, chunk_end in day_chunks(from_date, to_date):
        params = {
            "exchange": EXCHANGE, "segment": SEGMENT, "groww_symbol": groww_symbol,
            "start_time": f"{chunk_start:%Y-%m-%d} 09:15:00",
            "end_time": f"{chunk_end:%Y-%m-%d} 15:30:00",
            "candle_interval": CANDLE_INTERVAL,
        }
        response = _request_json(
            _query(HISTORICAL_CANDLES_PATH, params),
            access_token=access_token, transport=transport, sleep=sleep,
        )
        try:
            chunk = _validate_candles(response["payload"]["candles"])
        except (KeyError, TypeError) as exc:
            raise GrowwApiError("Groww candle response differs from documented payload.candles list") from exc
        candles.extend(chunk)
    by_timestamp = {str(candle[0]): candle for candle in candles}
    return [by_timestamp[key] for key in sorted(by_timestamp)]


def candles_to_frame(candles: Sequence[Sequence[Any]]):
    """Parse Groww's timezone-less documented timestamps as presumed IST."""
    return _candles_to_frame(candles, naive_timezone=IST)


def should_rescale_groww_oi(
    trade_date: str | date,
    raw_last_nonzero_oi: float | None,
    calendar_row: Mapping[str, Any] | None = None,
) -> bool:
    """Return whether one fetched day's OI is in Groww's ×100-underreported regime.

    A calendar row lets a live fetch override the historical date fallback.  A
    raw ratio near one is therefore always a no-op, protecting the ingest if
    Groww fixes the API.  Ratios between the two regimes are left untouched so
    an uncertain fetch cannot silently corrupt the archive.
    """
    if raw_last_nonzero_oi is None or raw_last_nonzero_oi == 0:
        return False
    if calendar_row is not None:
        expected = calendar_row.get("front_oi")
        if expected is not None and not pd.isna(expected):
            ratio = float(expected) / float(raw_last_nonzero_oi)
            if GROWW_OI_NEAR_ONE_RATIO_MIN <= ratio <= GROWW_OI_NEAR_ONE_RATIO_MAX:
                return False
            if ratio >= GROWW_OI_X100_RATIO_MIN:
                return True
            return False
    return as_date(trade_date) >= GROWW_OI_X100_START_DATE


def rescale_groww_oi(
    frame: pd.DataFrame,
    *,
    calendar_rows: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Scale OI ×100 for affected fetched days and return changed dates."""
    if frame.empty:
        return frame.copy(), []
    work = frame.copy()
    if "date" not in work or "oi" not in work:
        return work, []
    changed: list[str] = []
    for trade_day, indexes in work.groupby(work["date"].astype(str)).groups.items():
        oi = pd.to_numeric(work.loc[indexes, "oi"], errors="coerce")
        nonzero = oi[oi.ne(0) & oi.notna()]
        last_nonzero = float(nonzero.iloc[-1]) if not nonzero.empty else None
        row = calendar_rows.get(trade_day) if calendar_rows else None
        if should_rescale_groww_oi(trade_day, last_nonzero, row):
            work.loc[indexes, "oi"] = oi * 100
            changed.append(trade_day)
    return work, changed


def candles_to_futures_csv(
    candles: Sequence[Sequence[Any]] | pd.DataFrame,
    *,
    calendar_rows: Mapping[str, Mapping[str, Any]] | None = None,
):
    frame = candles if isinstance(candles, pd.DataFrame) else candles_to_frame(candles)
    converted = _candles_to_futures_csv(frame)
    converted, changed = rescale_groww_oi(converted, calendar_rows=calendar_rows)
    # The caller can turn this into a report warning without changing the CSV
    # schema or the established conversion return type.
    converted.attrs["groww_oi_rescaled_dates"] = changed
    return converted


def probe(
    expiry: str | date,
    *,
    access_token: str,
    transport: Transport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    contract = resolve_future_contract(expiry, access_token=access_token, transport=transport, sleep=sleep)
    candles = fetch_minute_candles(contract, expiry, expiry, access_token=access_token, transport=transport, sleep=sleep)
    frame = candles_to_futures_csv(candles)
    oi_nonzero = bool(not frame.empty and (frame["oi"] != 0).any())
    result = {
        "expiry": as_date(expiry).isoformat(), "contract": contract, "bar_count": len(frame),
        "first": frame.iloc[0].to_dict() if not frame.empty else None,
        "last": frame.iloc[-1].to_dict() if not frame.empty else None,
        "oi_nonzero": oi_nonzero,
    }
    print(f"Groww probe: expiry={result['expiry']} contract={contract}")
    print(f"bars={result['bar_count']} first={result['first']} last={result['last']} oi_nonzero={oi_nonzero}")
    return result
