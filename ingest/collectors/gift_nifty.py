"""GIFT Nifty minute collector; feed integration intentionally awaits a vendor."""
from __future__ import annotations

from typing import Any, Callable

from ._shared import dry_result, persist, schema, unpack


SCHEMA = schema("ts", "open", "high", "low", "close", "volume")
STUB_REQUIREMENT = "Configure a licensed NSE IX/GIFT Nifty 1-minute feed or vendor credential; no public endpoint is assumed."


def fetch_gift_nifty() -> dict[str, Any]:
    """Stub: replace with an NSE IX/GIFT feed adapter after licensing."""
    return {"records": [], "raw": {"status": "stub", "needs": STUB_REQUIREMENT}}


def collect_once(*, lake_root: str | None = None, fetcher: Callable[[], Any] | None = None, dry_run: bool = False) -> dict[str, Any]:
    if dry_run:
        return dry_result(SCHEMA, note=STUB_REQUIREMENT)
    rows, raw = unpack((fetcher or fetch_gift_nifty)())
    for row in rows:
        row["source_ts"] = row.get("source_ts") or row.get("exchange_ts") or row.get("ts")
        row["exchange_ts"] = row.get("exchange_ts") or row["source_ts"]
    return persist("gift_nifty", SCHEMA, rows, raw, lake_root=lake_root)
