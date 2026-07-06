"""Point-in-time index constituent contracts.

Constituent intervals use inclusive ``effective_from`` and exclusive
``effective_to`` boundaries. A missing ``effective_to`` is open-ended.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


CONSTITUENT_HISTORY_FORMAT = "meridian.pit-constituents.v1"


def load_constituent_history(source: Path | str | Iterable[dict[str, Any]], *, index_name: str | None = None) -> list[dict[str, Any]]:
    """Load normalized PIT constituent rows from CSV, JSON, JSONL, or mappings."""
    source_path: Path | None = None
    source_sha256: str | None = None
    if isinstance(source, (str, Path)):
        source_path = Path(source)
        source_sha256 = _sha256_file(source_path)
        rows = _read_rows(source_path)
    else:
        rows = list(source)

    normalized = [_normalize_row(row, source_path, source_sha256, index_name) for row in rows]
    normalized.sort(key=lambda row: (row["index_name"], row["symbol"], row["effective_from"], row.get("effective_to") or "9999-12-31"))
    return normalized


def tradable_on(history: Iterable[dict[str, Any]], symbol: str, as_of: str, *, index_name: str | None = None) -> bool:
    """Return whether ``symbol`` is a member on ``as_of``."""
    symbol_key = str(symbol).upper()
    date_key = _date_key(as_of)
    for row in history:
        if str(row.get("symbol", "")).upper() != symbol_key:
            continue
        if index_name is not None and str(row.get("index_name")) != str(index_name):
            continue
        if _active_on(row, date_key):
            return True
    return False


def filter_tradable_rows(
    rows: Iterable[dict[str, Any]],
    history: Iterable[dict[str, Any]],
    *,
    date_column: str = "timestamp",
    symbol_column: str = "symbol",
    index_name: str | None = None,
) -> list[dict[str, Any]]:
    """Keep trade/feature rows whose symbols are PIT index members on row date."""
    constituent_rows = list(history)
    kept: list[dict[str, Any]] = []
    for row in rows:
        symbol = row.get(symbol_column)
        as_of = row.get(date_column)
        if symbol is None or as_of is None:
            continue
        if tradable_on(constituent_rows, str(symbol), str(as_of), index_name=index_name):
            kept.append(dict(row))
    return kept


def constituent_manifest(history: Iterable[dict[str, Any]], *, source: Path | str | None = None) -> dict[str, Any]:
    """Build a JSON-serializable provenance manifest for constituent history."""
    rows = list(history)
    identity = {
        "format": CONSTITUENT_HISTORY_FORMAT,
        "source_path": str(source) if source is not None else _common_value(rows, "source_path"),
        "source_sha256": _sha256_file(Path(source)) if source is not None and Path(source).is_file() else _common_value(rows, "source_sha256"),
        "row_count": len(rows),
        "indices": sorted({str(row["index_name"]) for row in rows}),
        "symbols": sorted({str(row["symbol"]) for row in rows}),
        "interval_boundary": {"effective_from": "inclusive", "effective_to": "exclusive", "null_effective_to": "open_ended"},
        "rows_sha256": _rows_sha256(rows),
    }
    return {**identity, "manifest_id": _sha256_json(identity)}


def write_constituent_manifest(path: Path | str, history: Iterable[dict[str, Any]], *, source: Path | str | None = None) -> dict[str, Any]:
    manifest = constituent_manifest(history, source=source)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _read_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open(newline="", encoding="utf-8") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    if suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if suffix == ".json":
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
            return list(payload["rows"])
        if isinstance(payload, list):
            return payload
    raise ValueError(f"unsupported constituent history format: {path}")


def _normalize_row(row: dict[str, Any], source_path: Path | None, source_sha256: str | None, index_name: str | None) -> dict[str, Any]:
    idx = row.get("index_name", index_name)
    symbol = row.get("symbol")
    effective_from = row.get("effective_from")
    if not idx:
        raise ValueError(f"constituent row missing index_name: {row}")
    if not symbol:
        raise ValueError(f"constituent row missing symbol: {row}")
    if not effective_from:
        raise ValueError(f"constituent row missing effective_from: {row}")

    effective_to = _optional_date_key(row.get("effective_to"))
    normalized = {
        "index_name": str(idx),
        "symbol": str(symbol).upper(),
        "effective_from": _date_key(str(effective_from)),
        "effective_to": effective_to,
        "source_effective_date": _optional_date_key(row.get("source_effective_date")) or _date_key(str(effective_from)),
        "source_path": str(row.get("source_path") or source_path or ""),
        "source_sha256": str(row.get("source_sha256") or row.get("hash") or source_sha256 or ""),
    }
    normalized["row_hash"] = _sha256_json(normalized)
    return normalized


def _active_on(row: dict[str, Any], date_key: str) -> bool:
    if date_key < str(row["effective_from"]):
        return False
    effective_to = row.get("effective_to")
    return not effective_to or date_key < str(effective_to)


def _date_key(value: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError("date value cannot be empty")
    return text[:10]


def _optional_date_key(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nan"}:
        return None
    return _date_key(text)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _rows_sha256(rows: list[dict[str, Any]]) -> str:
    stable = [{key: row.get(key) for key in sorted(row) if key != "row_hash"} for row in rows]
    return _sha256_json(stable)


def _common_value(rows: list[dict[str, Any]], key: str) -> str | None:
    values = {str(row.get(key, "")) for row in rows if row.get(key)}
    return next(iter(values)) if len(values) == 1 else None
