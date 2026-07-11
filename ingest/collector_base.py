"""Append-only storage primitives for live, outcome-blind collectors.

The collector lake is deliberately simple: newline-delimited JSON records plus
the verbatim response body.  A manifest is the audit log; it is appended to
for both writes and idempotent duplicate skips.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


DEFAULT_LAKE_ROOT = Path("./collector_lake")
TIMESTAMP_COLUMNS = ("source_ts", "exchange_ts", "receive_ts")


def utc_now() -> str:
    """Return a timezone-aware UTC timestamp suitable for persisted records."""
    return datetime.now(timezone.utc).isoformat()


def lake_root(value: str | Path | None = None) -> Path:
    """Resolve the lake root without importing or mutating a .env file."""
    return Path(value or os.environ.get("COLLECTOR_LAKE_ROOT") or DEFAULT_LAKE_ROOT)


def dual_timestamp_record(
    record: Mapping[str, Any], source_ts: Any | None = None, *, receive_ts: str | None = None
) -> dict[str, Any]:
    """Add native source/exchange and local-receive timestamps to a record.

    ``source_ts`` is kept verbatim because it is the timestamp as published by
    the feed.  ``exchange_ts`` is supplied as the same native value when the
    source has no distinct exchange field, making the distinction explicit.
    """
    row = dict(record)
    native = source_ts if source_ts not in (None, "") else row.get("source_ts") or row.get("exchange_ts")
    native_text = "" if native is None else str(native)
    row["source_ts"] = str(row.get("source_ts") or native_text)
    row["exchange_ts"] = str(row.get("exchange_ts") or native_text)
    row["receive_ts"] = _utc_iso(receive_ts) if receive_ts else utc_now()
    return row


def append_observation(
    collector: str,
    records: Iterable[Mapping[str, Any]],
    *,
    raw: str | bytes | Mapping[str, Any] | list[Any],
    lake_root: str | Path | None = None,
    partition: str | None = None,
    receive_ts: str | None = None,
) -> dict[str, Any]:
    """Persist one immutable observation batch or append an idempotent skip.

    A duplicate is determined from the verbatim raw body, not normalized rows;
    an upstream correction therefore remains a new observation even if its
    parsed values happen to match a previous row.
    """
    root = lake_root_fn(lake_root)
    collector_root = root / "collectors" / collector
    collector_root.mkdir(parents=True, exist_ok=True)
    raw_text = _raw_text(raw)
    raw_hash = _sha256(raw_text.encode("utf-8"))
    received = _utc_iso(receive_ts) if receive_ts else utc_now()
    manifest = collector_root / "manifest.jsonl"
    entries = _manifest_entries(manifest)
    partition_key = _safe_partition(partition) if partition else None
    if any(
        entry.get("raw_sha256") == raw_hash and entry.get("action") == "written"
        and entry.get("partition") == partition_key
        for entry in entries
    ):
        entry = {
            "action": "skipped", "file": None, "sha256": None, "rows": 0,
            "first_ts": None, "last_ts": None, "raw_sha256": raw_hash,
            "note": "duplicate_raw_payload", "partition": partition_key, "written_at": received,
        }
        _append_manifest(manifest, entry)
        return entry

    rows = [dual_timestamp_record(row, receive_ts=received) for row in records]
    for row in rows:
        row.setdefault("raw", raw_text)
    sequence = max((int(item.get("sequence", 0)) for item in entries), default=0) + 1
    day = _parse_datetime(received).date()
    directory = collector_root
    directory /= f"{day:%Y}"
    directory /= f"{day:%m}"
    directory /= f"{day:%d}"
    if partition:
        directory /= _safe_partition(partition)
    directory.mkdir(parents=True, exist_ok=True)
    data_path = directory / f"{sequence:06d}.jsonl"
    raw_path = directory / f"{sequence:06d}.raw"
    # Exclusive creation enforces append-only behaviour even when the caller
    # accidentally retries while another process is writing.
    with raw_path.open("x", encoding="utf-8") as handle:
        handle.write(raw_text)
    data_text = "".join(json.dumps(row, sort_keys=True, default=str, separators=(",", ":")) + "\n" for row in rows)
    with data_path.open("x", encoding="utf-8") as handle:
        handle.write(data_text)
    source_times = [str(row.get("source_ts") or row.get("exchange_ts") or "") for row in rows]
    relative = data_path.relative_to(collector_root).as_posix()
    entry = {
        "action": "written", "sequence": sequence, "file": relative,
        "raw_file": raw_path.relative_to(collector_root).as_posix(),
        "sha256": _sha256(data_text.encode("utf-8")), "raw_sha256": raw_hash,
        "rows": len(rows), "first_ts": min(source_times, default=None),
        "last_ts": max(source_times, default=None), "partition": partition_key, "written_at": received,
    }
    _append_manifest(manifest, entry)
    return entry


def latest_records(
    collector: str, *, lake_root: str | Path | None = None, as_of: str | datetime | None = None
) -> list[dict[str, Any]]:
    """Return the newest received record per instrument from a collector lake."""
    root = lake_root_fn(lake_root) / "collectors" / collector
    cutoff = _parse_datetime(as_of) if as_of else None
    best: dict[str, dict[str, Any]] = {}
    for entry in _manifest_entries(root / "manifest.jsonl"):
        if entry.get("action") != "written" or not entry.get("file"):
            continue
        path = root / str(entry["file"])
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            received = _parse_datetime(row["receive_ts"])
            if cutoff and received > cutoff:
                continue
            key = str(row.get("instrument") or row.get("symbol") or "__collector__")
            if key not in best or received > _parse_datetime(best[key]["receive_ts"]):
                best[key] = row
    return list(best.values())


def lake_root_fn(value: str | Path | None = None) -> Path:
    """Private-name-compatible resolver kept separate from the public helper."""
    return lake_root(value)


def _raw_text(raw: str | bytes | Mapping[str, Any] | list[Any]) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8")
    if isinstance(raw, str):
        return raw
    return json.dumps(raw, sort_keys=True, default=str, separators=(",", ":"))


def _manifest_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _append_manifest(path: Path, entry: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(entry), sort_keys=True, separators=(",", ":")) + "\n")


def _parse_datetime(value: str | datetime) -> datetime:
    timestamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError(f"timestamp must include a timezone: {value!r}")
    return timestamp.astimezone(timezone.utc)


def _utc_iso(value: str | datetime) -> str:
    return _parse_datetime(value).isoformat()


def _safe_partition(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-=" else "_" for char in value)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()
