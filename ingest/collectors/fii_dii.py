"""Append-only FII/DII release artifacts using the existing PIT normalizer."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from ingest.fii_dii import parse_fii_dii_payload

from ._shared import dry_result, persist, schema, unpack


SCHEMA = schema("date", "fii_buy", "fii_sell", "fii_net", "dii_buy", "dii_sell", "dii_net", "total_inst_net", "released_at", "usable_from", "source")
STUB_REQUIREMENT = "A documented daily NSE/NSDL FII/DII release feed (and its credential if licensed); retain the original release artifact and append corrections."


def fetch_fii_dii() -> dict[str, Any]:
    """Stub: source URL/provenance is intentionally not guessed."""
    return {"records": [], "raw": {"status": "stub", "needs": STUB_REQUIREMENT}}


def collect_once(*, lake_root: str | None = None, fetcher: Callable[[], Any] | None = None, dry_run: bool = False) -> dict[str, Any]:
    if dry_run:
        return dry_result(SCHEMA, note=STUB_REQUIREMENT)
    payload, raw = unpack((fetcher or fetch_fii_dii)())
    # Injected fetchers may return pre-normalized records; otherwise reuse the
    # established release-time and flow parser from ingest.fii_dii.
    rows = payload if payload and "released_at" in payload[0] else parse_fii_dii_payload(payload, fetched_at=datetime.now(timezone.utc))
    for row in rows:
        row["source"] = row.get("source") or "FII_DII_RELEASE"
        row["source_ts"] = row.get("source_ts") or row.get("released_at")
        row["exchange_ts"] = row.get("exchange_ts") or row["source_ts"]
    return persist("fii_dii", SCHEMA, rows, raw, lake_root=lake_root)
