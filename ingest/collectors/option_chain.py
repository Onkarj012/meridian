"""NIFTY option-chain snapshot skeleton with a complete per-leg contract."""
from __future__ import annotations

from typing import Any, Callable

from ._shared import dry_result, persist, schema, unpack


SCHEMA = schema("underlying", "underlying_value", "snapshot_ts", "expiry", "strike", "option_type", "bid", "ask", "ltp", "oi", "iv", "volume")
STUB_REQUIREMENT = "NSE public option-chain endpoint for NIFTY, with a session cookie, conservative rate limiting, and retry/backoff; use a licensed feed for production SLA."


def fetch_option_chain() -> dict[str, Any]:
    """Stub: no NSE request is made until rate-limit/session handling is operated."""
    return {"records": [], "raw": {"status": "stub", "needs": STUB_REQUIREMENT}}


def collect_once(*, lake_root: str | None = None, fetcher: Callable[[], Any] | None = None, dry_run: bool = False) -> dict[str, Any]:
    if dry_run:
        return dry_result(SCHEMA, note=STUB_REQUIREMENT)
    rows, raw = unpack((fetcher or fetch_option_chain)())
    for row in rows:
        row["source_ts"] = row.get("source_ts") or row.get("snapshot_ts")
        row["exchange_ts"] = row.get("exchange_ts") or row["source_ts"]
    return persist("option_chain", SCHEMA, rows, raw, lake_root=lake_root)
