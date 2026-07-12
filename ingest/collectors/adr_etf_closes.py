"""Append-only daily ADR/ETF close and corporate-action observations."""
from __future__ import annotations

import json
from typing import Any, Callable

from ._shared import dry_result, persist, schema, unpack


SCHEMA = schema("instrument", "trade_date", "close", "adj_close", "currency", "record_type", "action_type", "action_value", "provider")
YFINANCE_TICKERS = {"INFY": "INFY", "HDB": "HDB", "IBN": "IBN", "WIT": "WIT", "INDA": "INDA", "EPI": "EPI"}
STUB_REQUIREMENT = "yfinance supplies daily close/action observations where Yahoo publishes them; a licensed US close and corporate-action feed is needed for authoritative coverage."


def fetch_yfinance_closes() -> tuple[list[dict[str, Any]], str]:
    try:
        import yfinance as yf  # type: ignore
    except ImportError:
        return [], json.dumps({"status": "unavailable", "needs": "optional yfinance package or licensed daily-close vendor"})
    rows: list[dict[str, Any]] = []
    raw: dict[str, Any] = {"provider": "yfinance", "rows": []}
    for instrument, ticker in YFINANCE_TICKERS.items():
        frame = yf.Ticker(ticker).history(period="10d", auto_adjust=False, actions=True)
        for timestamp, item in frame.iterrows():
            trade_date = str(timestamp.date())
            base = {"instrument": instrument, "trade_date": trade_date, "close": item.get("Close"), "adj_close": item.get("Adj Close"), "currency": "USD", "record_type": "close", "action_type": None, "action_value": None, "provider": "yfinance", "source_ts": trade_date, "exchange_ts": trade_date}
            rows.append(base)
            for column, action_type in (("Dividends", "dividend"), ("Stock Splits", "split")):
                value = item.get(column, 0)
                if value:
                    rows.append({**base, "record_type": "corporate_action", "action_type": action_type, "action_value": value})
    raw["rows"] = rows
    return rows, json.dumps(raw, sort_keys=True, separators=(",", ":"), default=str)


def collect_once(*, lake_root: str | None = None, fetcher: Callable[[], Any] | None = None, dry_run: bool = False) -> dict[str, Any]:
    if dry_run:
        return dry_result(SCHEMA, note=STUB_REQUIREMENT)
    rows, raw = unpack((fetcher or fetch_yfinance_closes)())
    outputs = []
    for instrument in sorted({str(row.get("instrument") or "UNKNOWN") for row in rows}):
        batch = [row for row in rows if str(row.get("instrument") or "UNKNOWN") == instrument]
        for row in batch:
            row["source_ts"] = row.get("source_ts") or row.get("trade_date")
            row["exchange_ts"] = row.get("exchange_ts") or row["source_ts"]
        outputs.append(persist("adr_etf_closes", SCHEMA, batch, raw, lake_root=lake_root, partition_by="instrument"))
    return {"action": "written" if outputs else "no_data", "schema": SCHEMA, "batches": outputs, "records": sum(item.get("rows", 0) for item in outputs)}
