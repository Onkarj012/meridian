"""Append-only wrapper around the existing NSE corporate-announcement parser."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable

from zoneinfo import ZoneInfo

from ._shared import dry_result, persist, schema, unpack


SCHEMA = schema("symbol", "headline", "published_at", "available_at", "exchange_seq_id", "event_type_hint", "attachment_url", "source")
STUB_REQUIREMENT = "NSE's public corporate-announcements endpoint, including its browser-like headers and rate limits; no credential is stored in this collector."


def fetch_nse_announcements() -> tuple[list[dict[str, Any]], str]:
    """Reuse the live-capable fetch/parser in ``ingest.announcements`` for today."""
    # announcements.py has optional Polars-based archival helpers; importing it
    # only on the live path keeps dry-run/schema checks dependency-free.
    from ingest.announcements import _fetch_nse_chunk, _normalize_nse_row

    today = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%d-%m-%Y")
    payload = _fetch_nse_chunk(today, today)
    rows = [row for item in payload if (row := _normalize_nse_row(item)) is not None]
    return rows, json.dumps(payload, sort_keys=True, default=str)


def collect_once(*, lake_root: str | None = None, fetcher: Callable[[], Any] | None = None, dry_run: bool = False) -> dict[str, Any]:
    if dry_run:
        return dry_result(SCHEMA, note=STUB_REQUIREMENT)
    rows, raw = unpack((fetcher or fetch_nse_announcements)())
    for row in rows:
        row["source_ts"] = row.get("source_ts") or row.get("available_at") or row.get("published_at")
        row["exchange_ts"] = row.get("exchange_ts") or row["source_ts"]
        row.setdefault("source", "NSE")
    return persist("nse_announcements", SCHEMA, rows, raw, lake_root=lake_root)
