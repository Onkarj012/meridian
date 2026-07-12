"""Point-in-time vector snapshots derived from immutable collector records."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ingest.collector_base import latest_records

from ._shared import dry_result, persist, schema


SCHEMA = schema("as_of_ts", "source_collector", "instrument", "quote_ts", "quote_age_seconds", "values_json")
SCHEDULE_IST = ("08:00", "09:00", "09:15", "09:45")
SOURCE_COLLECTORS = ("gift_nifty", "global_minute", "nse_announcements", "option_chain", "adr_etf_closes", "fii_dii")
CRON_LINES = (
    "CRON_TZ=Asia/Kolkata",
    "0 8 * * 1-5 $MERIDIAN_ROOT/.venv/bin/python $MERIDIAN_ROOT/scripts/collectors/snapshot_ledger.py",
    "0 9 * * 1-5 $MERIDIAN_ROOT/.venv/bin/python $MERIDIAN_ROOT/scripts/collectors/snapshot_ledger.py",
    "15 9 * * 1-5 $MERIDIAN_ROOT/.venv/bin/python $MERIDIAN_ROOT/scripts/collectors/snapshot_ledger.py",
    "45 9 * * 1-5 $MERIDIAN_ROOT/.venv/bin/python $MERIDIAN_ROOT/scripts/collectors/snapshot_ledger.py",
)


def collect_once(*, lake_root: str | None = None, as_of: str | datetime | None = None, dry_run: bool = False) -> dict[str, Any]:
    if dry_run:
        return dry_result(SCHEMA, note="Run from cron at 08:00, 09:00, 09:15 and 09:45 IST; scheduling is an ops responsibility.")
    capture = _as_utc(as_of)
    rows: list[dict[str, Any]] = []
    for collector in SOURCE_COLLECTORS:
        for record in latest_records(collector, lake_root=lake_root, as_of=capture):
            quote_ts = record.get("source_ts") or record.get("exchange_ts")
            rows.append({
                "as_of_ts": capture, "source_collector": collector,
                "instrument": record.get("instrument") or record.get("symbol") or collector,
                "quote_ts": quote_ts, "quote_age_seconds": _quote_age(capture, quote_ts),
                "values_json": json.dumps({key: value for key, value in record.items() if key not in {"raw", "receive_ts"}}, sort_keys=True, default=str),
                "source_ts": quote_ts, "exchange_ts": quote_ts,
            })
    return persist("snapshot_ledger", SCHEMA, rows, json.dumps(rows, sort_keys=True, default=str), lake_root=lake_root, receive_ts=capture)


def _as_utc(value: str | datetime | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _quote_age(as_of: str, quote_ts: Any) -> float | None:
    if not quote_ts:
        return None
    try:
        quote = datetime.fromisoformat(str(quote_ts).replace("Z", "+00:00"))
        if quote.tzinfo is None:
            return None
        return (datetime.fromisoformat(as_of).astimezone(timezone.utc) - quote.astimezone(timezone.utc)).total_seconds()
    except ValueError:
        return None
