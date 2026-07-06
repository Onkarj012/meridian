"""Deterministic, auditable contracts for local minute-market sources."""
from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lake.market import REQUIRED_COLUMNS, normalize_row, parse_timestamp


SNAPSHOT_FORMAT = "meridian.market-source-snapshot.v1"
_CORRECTION_WORDS = r"(?:corrected|correction|corrective|fixed|fix)"
_NORMAL_EQUITY = re.compile(r"^(?P<symbol>[A-Za-z0-9&.-]+)_minute\.csv$", re.IGNORECASE)
_CORRECTED_EQUITY = re.compile(
    rf"^(?P<symbol>[A-Za-z0-9&.-]+)[_ .-]{_CORRECTION_WORDS}_minute\.csv$"
    rf"|^(?P<suffix_symbol>[A-Za-z0-9&.-]+)_minute[_ .-]{_CORRECTION_WORDS}\.csv$",
    re.IGNORECASE,
)
_PERMITTED_INDICES = {"NIFTY", "BANKNIFTY"}


def build_source_snapshot(source_root: Path, output_dir: Path) -> dict[str, Any]:
    """Create (or return) the immutable source snapshot for ``source_root``.

    The identity intentionally excludes mtimes and output locations: it is based
    on the source contract decisions and source content hashes only.  Mtimes are
    retained in the audit record for operators investigating a local source.
    """
    source_root = source_root.resolve()
    if not source_root.is_dir():
        raise ValueError(f"source root must be a directory: {source_root}")

    records = [_inspect_source(source_root, path) for path in sorted(source_root.rglob("*")) if path.is_file()]
    _resolve_equity_collisions(records)
    records.sort(key=lambda item: item["path"])

    identity_payload = {
        "format": SNAPSHOT_FORMAT,
        "sources": [_identity_source(record) for record in records],
    }
    snapshot_id = _sha256_json(identity_payload)
    selected = [record for record in records if record["selection_decision"].startswith("selected")]
    trust = _trust_summary(records)
    snapshot = {
        "format": SNAPSHOT_FORMAT,
        "snapshot_id": snapshot_id,
        "source_root": ".",
        "sources": records,
        "selected_source_count": len(selected),
        "trust_summary": trust,
    }

    snapshot_path = output_dir / "source-snapshots" / f"{snapshot_id}.json"
    trust_path = output_dir / "source-trust" / f"{snapshot_id}.json"
    _write_immutable_json(snapshot_path, snapshot)
    _write_immutable_json(trust_path, {"format": SNAPSHOT_FORMAT, "snapshot_id": snapshot_id, **trust})
    return {
        "snapshot_id": snapshot_id,
        "snapshot": str(snapshot_path),
        "source_trust": str(trust_path),
        "selected_source_count": len(selected),
        **trust["counts"],
    }


def _inspect_source(source_root: Path, path: Path) -> dict[str, Any]:
    relative_path = path.relative_to(source_root).as_posix()
    stat = path.stat()
    record: dict[str, Any] = {
        "logical_role": "uncontracted",
        "path": relative_path,
        "byte_size": stat.st_size,
        "modification_time": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "sha256": _sha256_file(path),
        "schema": {"name": "not_csv", "columns": [], "canonical_columns": [], "valid": False},
        "row_count": 0,
        "timestamp_bounds": {"start": None, "end": None},
        "selection_decision": "excluded_uncontracted_raw_archive",
    }
    parent = path.parent.name.lower()
    name = path.name
    lower_path = relative_path.lower()
    if "option" in lower_path or "bhavcopy" in lower_path:
        record["selection_decision"] = "excluded_option_or_bhavcopy"
        if path.suffix.lower() == ".csv":
            record["schema"], record["row_count"], record["timestamp_bounds"] = _inspect_csv(path)
        return record
    if any(part in {"bronze", "silver", "gold", "reports", "cache"} for part in Path(relative_path).parts) or name.startswith("market_candles"):
        record["selection_decision"] = "excluded_generated_artifact"
        if path.suffix.lower() == ".csv":
            record["schema"], record["row_count"], record["timestamp_bounds"] = _inspect_csv(path)
        return record
    if parent == "nifty500":
        corrected = _CORRECTED_EQUITY.match(name)
        normal = _NORMAL_EQUITY.match(name)
        if corrected or normal:
            symbol = (corrected.group("symbol") or corrected.group("suffix_symbol")) if corrected else normal.group("symbol")
            record["logical_role"] = "equity_minute_corrective" if corrected else "equity_minute"
            record["symbol"] = symbol.upper()
            record["schema"], record["row_count"], record["timestamp_bounds"] = _inspect_csv(path)
            record["selection_decision"] = "pending_equity_resolution"
            return record
    if parent == "indices_minute" and path.suffix.lower() == ".csv":
        symbol = _index_symbol(name)
        if symbol in _PERMITTED_INDICES:
            record["logical_role"] = "index_minute_context"
            record["symbol"] = symbol
            record["schema"], record["row_count"], record["timestamp_bounds"] = _inspect_csv(path)
            record["selection_decision"] = "selected_index_context" if record["schema"]["valid"] else "excluded_invalid_schema"
            return record
    if path.suffix.lower() == ".csv":
        record["schema"], record["row_count"], record["timestamp_bounds"] = _inspect_csv(path)
        record["selection_decision"] = "excluded_not_permitted_market_feed"
    return record


def _inspect_csv(path: Path) -> tuple[dict[str, Any], int, dict[str, str | None]]:
    rows = 0
    bounds: list[datetime] = []
    timestamps: list[str] = []
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            columns = reader.fieldnames or []
            canonical_columns = sorted(normalize_row({column: "" for column in columns}))
            required = set(REQUIRED_COLUMNS) - {"symbol"}
            valid = required.issubset(canonical_columns)
            for row in reader:
                rows += 1
                timestamp = normalize_row(row).get("timestamp")
                if timestamp:
                    try:
                        parsed = parse_timestamp(timestamp)
                        bounds.append(parsed)
                        timestamps.append(parsed.isoformat())
                    except ValueError:
                        pass
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        return ({"name": "unreadable_csv", "columns": [], "canonical_columns": [], "valid": False, "error": str(exc)}, 0, {"start": None, "end": None})
    return (
        {
            "name": "minute_ohlcv_v1" if valid else "unrecognized_csv",
            "columns": columns,
            "canonical_columns": canonical_columns,
            "valid": valid,
        },
        rows,
        {
            "start": min(bounds).isoformat() if bounds else None,
            "end": max(bounds).isoformat() if bounds else None,
            "coverage_sha256": _sha256_json(sorted(timestamps)),
        },
    )


def _resolve_equity_collisions(records: list[dict[str, Any]]) -> None:
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record["logical_role"] in {"equity_minute", "equity_minute_corrective"}:
            by_symbol[record["symbol"]].append(record)
    for candidates in by_symbol.values():
        normal = [item for item in candidates if item["logical_role"] == "equity_minute"]
        corrective = [item for item in candidates if item["logical_role"] == "equity_minute_corrective"]
        if len(normal) == 1 and not corrective:
            normal[0]["selection_decision"] = "selected_equity" if normal[0]["schema"]["valid"] else "excluded_invalid_schema"
        elif len(normal) == 0 and len(corrective) == 1:
            corrective[0]["selection_decision"] = "selected_corrected_equity" if corrective[0]["schema"]["valid"] else "excluded_invalid_schema"
        elif len(normal) == 1 and len(corrective) == 1 and _is_verified_replacement(normal[0], corrective[0]):
            comparison = _correction_comparison(normal[0], corrective[0])
            normal[0]["correction_comparison"] = comparison
            corrective[0]["correction_comparison"] = comparison
            normal[0]["selection_decision"] = "corrected_superseded"
            corrective[0]["selection_decision"] = "selected_corrected_equity"
        else:
            comparison = _correction_comparison(normal[0], corrective[0]) if len(normal) == len(corrective) == 1 else None
            for item in candidates:
                item["selection_decision"] = "quarantined_source_collision"
                item["collision"] = _collision_evidence(candidates)
                if comparison is not None:
                    item["correction_comparison"] = comparison


def _is_verified_replacement(normal: dict[str, Any], corrective: dict[str, Any]) -> bool:
    if not normal["schema"]["valid"] or not corrective["schema"]["valid"]:
        return False
    if normal["schema"]["canonical_columns"] != corrective["schema"]["canonical_columns"]:
        return False
    # Full timestamp-bound coverage and a non-empty overlap make the replacement
    # explicit. Partial patches are quarantined rather than silently joined.
    normal_bounds, corrective_bounds = normal["timestamp_bounds"], corrective["timestamp_bounds"]
    return (
        normal["row_count"] == corrective["row_count"]
        and normal["row_count"] > 0
        and normal_bounds == corrective_bounds
        and normal_bounds["start"] is not None
    )


def _collision_evidence(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "paths": [item["path"] for item in sorted(candidates, key=lambda item: item["path"])],
        "schemas": [item["schema"]["canonical_columns"] for item in candidates],
        "coverage": [item["timestamp_bounds"] for item in candidates],
        "overlap": _coverage_overlap(candidates),
    }


def _correction_comparison(normal: dict[str, Any], corrective: dict[str, Any]) -> dict[str, Any]:
    return {
        "normal_path": normal["path"],
        "corrective_path": corrective["path"],
        "schema_matches": normal["schema"]["canonical_columns"] == corrective["schema"]["canonical_columns"],
        "coverage_matches": normal["timestamp_bounds"].get("coverage_sha256") == corrective["timestamp_bounds"].get("coverage_sha256"),
        "row_count_matches": normal["row_count"] == corrective["row_count"],
        "overlap": _coverage_overlap([normal, corrective]),
    }


def _coverage_overlap(candidates: list[dict[str, Any]]) -> bool:
    bounds = [item["timestamp_bounds"] for item in candidates]
    if any(bound["start"] is None or bound["end"] is None for bound in bounds):
        return False
    return max(bound["start"] for bound in bounds) <= min(bound["end"] for bound in bounds)


def _trust_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    grouped = {
        "selected": [item["path"] for item in records if item["selection_decision"].startswith("selected")],
        "excluded": [item["path"] for item in records if item["selection_decision"].startswith("excluded")],
        "corrected": [item["path"] for item in records if item["selection_decision"] == "selected_corrected_equity"],
        "quarantined": [item["path"] for item in records if item["selection_decision"].startswith("quarantined")],
    }
    return {"counts": {name: len(paths) for name, paths in grouped.items()}, **grouped}


def _identity_source(record: dict[str, Any]) -> dict[str, Any]:
    return {key: record[key] for key in ("logical_role", "path", "sha256", "schema", "row_count", "timestamp_bounds", "selection_decision")}


def _index_symbol(name: str) -> str:
    stem = Path(name).stem.upper()
    stem = re.sub(r"(?:_MINUTE|_1MIN|_1M)$", "", stem)
    return stem.replace(" ", "")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _write_immutable_json(path: Path, value: object) -> None:
    encoded = json.dumps(value, indent=2, sort_keys=True) + "\n"
    if path.exists():
        if path.read_text(encoding="utf-8") != encoded:
            raise RuntimeError(f"immutable source snapshot collision: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded, encoding="utf-8")
