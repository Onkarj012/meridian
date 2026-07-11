"""Best-effort yfinance minute collector for global overnight instruments."""
from __future__ import annotations

import json
from typing import Any, Callable

from ._shared import dry_result, persist, schema, unpack


SCHEMA = schema("instrument", "ts", "open", "high", "low", "close", "volume", "provider")
YFINANCE_TICKERS = {"ES": "ES=F", "NQ": "NQ=F", "NIKKEI": "^N225", "KOSPI": "^KS11", "HSI": "^HSI", "USDINR": "INR=X", "BRENT": "BZ=F"}
STUB_REQUIREMENT = "yfinance supplies best-effort recent 1-minute bars where Yahoo supports the symbol; use a licensed vendor for complete exchange-grade coverage."


def fetch_yfinance_minute() -> tuple[list[dict[str, Any]], str]:
    """Fetch Yahoo-supported recent 1-minute bars without making it archival truth."""
    try:
        import yfinance as yf  # type: ignore
    except ImportError:
        return [], json.dumps({"status": "unavailable", "needs": "optional yfinance package or licensed minute-bar vendor"})
    rows: list[dict[str, Any]] = []
    raw: dict[str, Any] = {"provider": "yfinance", "instruments": {}}
    for instrument, ticker in YFINANCE_TICKERS.items():
        frame = yf.download(ticker, period="1d", interval="1m", progress=False, auto_adjust=False, threads=False)
        if frame.empty:
            raw["instruments"][instrument] = 0
            continue
        frame = frame.reset_index()
        frame.columns = [str(column[0] if isinstance(column, tuple) else column).lower().replace(" ", "_") for column in frame.columns]
        timestamp_column = "datetime" if "datetime" in frame.columns else "date"
        for item in frame.to_dict("records"):
            timestamp = str(item[timestamp_column])
            rows.append({"instrument": instrument, "ts": timestamp, "open": item.get("open"), "high": item.get("high"), "low": item.get("low"), "close": item.get("close"), "volume": item.get("volume"), "provider": "yfinance", "source_ts": timestamp, "exchange_ts": timestamp})
        raw["instruments"][instrument] = len(frame)
    return rows, json.dumps(raw, sort_keys=True)


def collect_once(*, lake_root: str | None = None, fetcher: Callable[[], Any] | None = None, dry_run: bool = False) -> dict[str, Any]:
    if dry_run:
        return dry_result(SCHEMA, note=STUB_REQUIREMENT)
    rows, raw = unpack((fetcher or fetch_yfinance_minute)())
    outputs = []
    # Per-instrument directories retain an append-only sequence under one
    # collector manifest, as required for independent quote inspection.
    for instrument in sorted({str(row.get("instrument") or "UNKNOWN") for row in rows}):
        instrument_rows = [row for row in rows if str(row.get("instrument") or "UNKNOWN") == instrument]
        for row in instrument_rows:
            row["source_ts"] = row.get("source_ts") or row.get("ts")
            row["exchange_ts"] = row.get("exchange_ts") or row["source_ts"]
        outputs.append(persist("global_minute", SCHEMA, instrument_rows, raw, lake_root=lake_root, partition_by="instrument"))
    return {"action": "written" if outputs else "no_data", "schema": SCHEMA, "batches": outputs, "records": sum(item.get("rows", 0) for item in outputs)}
