"""Resumable, quality-validated local Parquet/DuckDB market lake."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

from lake.market import REQUIRED_COLUMNS, normalize_row, parse_timestamp
from contracts.snapshot import SNAPSHOT_FORMAT


LAKE_FORMAT = "meridian.research-lake.v1"
VALIDATION_CONTRACT = "meridian.minute-candle-validation.v1"
IMPLEMENTATION_VERSION = "2026-06-23-resolved-quarantine-policy"
IST = ZoneInfo("Asia/Kolkata")
SESSION_START, SESSION_END = time(9, 15), time(15, 29)
# Kept as data rather than an implicit exception in the validator.  Add dates
# here only with an operator-visible reason in a future contract revision.
SESSION_EXCEPTIONS: dict[str, dict[str, str]] = {}
REJECTION_REASONS = {
    "duplicate_timestamp",
    "invalid_ohlc",
    "invalid_row",
    "negative_volume",
    "off_session",
    "outlier_return",
    "symbol_mismatch",
}
QUARANTINE_POLICY = {
    "version": "silver-excludes-material-breaches-v1",
    "resolved_by": "rows with rejection reasons are omitted from silver/gold/model inputs and retained in bronze diagnostics",
    "material_rejection_reasons": sorted(REJECTION_REASONS),
    "diagnostic_only_reasons": ["stale_price", "unexplained_gap"],
}


@dataclass(frozen=True)
class LakeBuildResult:
    rows: int
    partitions: int
    fingerprint: str
    manifest: str
    reused_partitions: int = 0
    quarantined_rows: int = 0


def dataset_fingerprint(paths: list[Path]) -> str:
    """Content fingerprint for legacy callers (never use mtimes for resumption)."""
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.read_bytes())
    return digest.hexdigest()


def build_snapshot_lake(
    snapshot: Path, source_root: Path, lake_root: Path, *, universe_manifest: Path | None = None,
) -> LakeBuildResult:
    """Materialize selected source-snapshot files into bronze and silver Parquet.

    ``source_root`` is explicit because source snapshots intentionally contain
    portable relative paths rather than a machine-specific absolute location.
    """
    snapshot_data = json.loads(snapshot.read_text(encoding="utf-8"))
    if snapshot_data.get("format") != SNAPSHOT_FORMAT:
        raise ValueError(f"unsupported source snapshot format: {snapshot_data.get('format')!r}")
    snapshot_id = str(snapshot_data["snapshot_id"])
    source_root, lake_root = source_root.resolve(), lake_root.resolve()
    selected = [source for source in snapshot_data.get("sources", []) if str(source.get("selection_decision", "")).startswith("selected")]
    universe_id = None
    if universe_manifest is not None:
        universe_data = json.loads(Path(universe_manifest).read_text(encoding="utf-8"))
        if universe_data.get("source_snapshot_id") != snapshot_id:
            raise ValueError("universe manifest does not belong to the requested source snapshot")
        symbols = {str(item["symbol"]).upper() for item in universe_data.get("symbols", [])}
        if not symbols:
            raise ValueError("universe manifest contains no symbols")
        selected = [source for source in selected if source["logical_role"] == "index_minute_context" or str(source.get("symbol", "")).upper() in symbols]
        universe_id = universe_data.get("manifest_id")

    total_rows = total_quarantined = partitions = reused = 0
    partition_reports: list[dict[str, Any]] = []
    for source in selected:
        source_path = source_root / source["path"]
        if not source_path.is_file():
            raise FileNotFoundError(f"snapshot source is missing: {source_path}")
        actual_hash = _sha256_file(source_path)
        if actual_hash != source["sha256"]:
            raise RuntimeError(f"snapshot source content changed: {source['path']}")
        asset_type = "index" if source["logical_role"] == "index_minute_context" else "equity"
        bronze_rows, silver_rows, diagnostics = _read_and_validate(source_path, str(source["symbol"]))
        grouped_bronze = _group_partitions(bronze_rows, asset_type)
        grouped_silver = _group_partitions(silver_rows, asset_type)
        keys = sorted(set(grouped_bronze) | set(grouped_silver))
        for key in keys:
            date, symbol, asset = key
            fingerprint = _partition_fingerprint(snapshot_id, source, date, asset)
            report = _write_partition(
                lake_root, asset, date, symbol, grouped_bronze.get(key, []), grouped_silver.get(key, []),
                fingerprint, snapshot_id, diagnostics,
            )
            partitions += 1
            reused += int(report["reused"])
            partition_reports.append(report)
        total_rows += len(silver_rows)
        total_quarantined += diagnostics["quarantined_rows"]

    root_manifest = {
        "format": LAKE_FORMAT,
        "snapshot_id": snapshot_id,
        "snapshot_format": snapshot_data["format"],
        "universe_manifest_id": universe_id,
        "validation_contract": VALIDATION_CONTRACT,
        "implementation_version": IMPLEMENTATION_VERSION,
        "quarantine_policy": QUARANTINE_POLICY,
        "session": {"timezone": "Asia/Kolkata", "start": "09:15", "end": "15:29", "exceptions": SESSION_EXCEPTIONS},
        "rows": total_rows,
        "partitions": partitions,
        "reused_partitions": reused,
        "quarantined_rows": total_quarantined,
        "partition_manifests": partition_reports,
    }
    manifest_path = lake_root / "manifest.json"
    _atomic_json(manifest_path, root_manifest)
    register_lake(lake_root)
    return LakeBuildResult(total_rows, partitions, _sha256_json(root_manifest), str(manifest_path), reused, total_quarantined)


def _read_and_validate(path: Path, expected_symbol: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    bronze: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    previous_timestamp: datetime | None = None
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        canonical = set(normalize_row({column: "" for column in columns}))
        missing = sorted(set(REQUIRED_COLUMNS) - {"symbol"} - canonical)
        if missing:
            raise ValueError(f"schema drift in {path}: missing {', '.join(missing)}")
        unexpected = sorted(canonical - set(REQUIRED_COLUMNS))
        if unexpected:
            counts["schema_drift_extra_columns"] += 1
        for row_number, raw in enumerate(reader, start=2):
            row = normalize_row(raw)
            try:
                item = _canonical_row(row, expected_symbol, row_number)
            except (TypeError, ValueError) as exc:
                _reject(rejected, counts, "invalid_row", row_number, str(exc), row)
                continue
            bronze.append(item)
            if previous_timestamp is not None and item["timestamp"] < previous_timestamp:
                counts["ordering_error"] += 1
            previous_timestamp = item["timestamp"]
            if item["symbol"] != expected_symbol:
                _reject(rejected, counts, "symbol_mismatch", row_number, "row symbol differs from source contract", item)
            elif not _in_session(item["timestamp"]):
                _reject(rejected, counts, "off_session", row_number, "outside continuous 09:15-15:29 session", item)
            elif not _valid_ohlc(item):
                _reject(rejected, counts, "invalid_ohlc", row_number, "OHLC values are inconsistent or non-positive", item)
            elif item["volume"] < 0:
                _reject(rejected, counts, "negative_volume", row_number, "volume must be non-negative", item)
            else:
                candidates.append(item)
    candidates.sort(key=lambda item: (item["timestamp"], item["source_row"]))
    deduped: list[dict[str, Any]] = []
    seen: set[datetime] = set()
    for item in candidates:
        if item["timestamp"] in seen:
            _reject(rejected, counts, "duplicate_timestamp", item["source_row"], "duplicate symbol/timestamp", item)
        else:
            seen.add(item["timestamp"])
            deduped.append(item)
    _diagnose_gaps_and_stale(deduped, counts)
    outliers = _outlier_rows(deduped)
    silver = []
    for item in deduped:
        if item["source_row"] in outliers:
            _reject(rejected, counts, "outlier_return", item["source_row"], "robust return outlier", item)
        else:
            silver.append(_parquet_row(item))
    bronze_out = [_parquet_row(item) for item in bronze]
    quarantined_rows = sum(counts[reason] for reason in REJECTION_REASONS)
    return bronze_out, silver, {"counts": dict(sorted(counts.items())), "quarantined_rows": quarantined_rows, "rejected_samples": rejected[:100]}


def _canonical_row(row: dict[str, str], expected_symbol: str, source_row: int) -> dict[str, Any]:
    missing = [name for name in ("timestamp", "open", "high", "low", "close", "volume") if not str(row.get(name, "")).strip()]
    if missing:
        raise ValueError(f"missing required columns: {', '.join(missing)}")
    raw_timestamp = parse_timestamp(row["timestamp"])
    # Validate syntax before assigning the only permitted market timezone.
    timestamp = raw_timestamp.replace(tzinfo=IST) if raw_timestamp.tzinfo is None else raw_timestamp.astimezone(IST)
    return {
        "symbol": row.get("symbol", expected_symbol).strip().upper() or expected_symbol,
        "timestamp": timestamp,
        "open": float(row["open"]), "high": float(row["high"]), "low": float(row["low"]),
        "close": float(row["close"]), "volume": float(row["volume"]),
        "source_row": source_row,
    }


def _in_session(timestamp: datetime) -> bool:
    exception = SESSION_EXCEPTIONS.get(timestamp.date().isoformat())
    if exception:
        return exception["start"] <= timestamp.strftime("%H:%M") <= exception["end"]
    return SESSION_START <= timestamp.time().replace(second=0, microsecond=0) <= SESSION_END and timestamp.second == 0


def _valid_ohlc(item: dict[str, Any]) -> bool:
    values = [item[name] for name in ("open", "high", "low", "close")]
    return all(value > 0 for value in values) and item["low"] <= min(item["open"], item["close"]) <= max(item["open"], item["close"]) <= item["high"]


def _diagnose_gaps_and_stale(rows: list[dict[str, Any]], counts: Counter[str]) -> None:
    by_day: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_day[(row["symbol"], row["timestamp"].date().isoformat())].append(row)
    for day_rows in by_day.values():
        stale = 0
        for previous, current in zip(day_rows, day_rows[1:]):
            if current["timestamp"] - previous["timestamp"] > timedelta(minutes=1):
                counts["unexplained_gap"] += 1
            stale = stale + 1 if current["close"] == previous["close"] else 0
            if stale >= 5:
                counts["stale_price"] += 1


def _outlier_rows(rows: list[dict[str, Any]]) -> set[int]:
    returns = []
    indexed = []
    for previous, current in zip(rows, rows[1:]):
        if previous["symbol"] == current["symbol"] and previous["timestamp"].date() == current["timestamp"].date():
            value = current["close"] / previous["close"] - 1
            returns.append(value)
            indexed.append((current["source_row"], value))
    if len(returns) < 5:
        return set()
    median = statistics.median(returns)
    mad = statistics.median(abs(value - median) for value in returns)
    if mad == 0:
        return {row for row, value in indexed if abs(value - median) > 0.05}
    return {row for row, value in indexed if abs(value - median) / (1.4826 * mad) > 8}


def _reject(samples: list[dict[str, Any]], counts: Counter[str], reason: str, row_number: int, detail: str, row: dict[str, Any]) -> None:
    counts[reason] += 1
    if len(samples) < 100:
        samples.append({"reason": reason, "row": row_number, "detail": detail, "timestamp": str(row.get("timestamp", "")), "symbol": row.get("symbol")})


def _parquet_row(row: dict[str, Any]) -> dict[str, Any]:
    return {name: (value.isoformat() if name == "timestamp" and hasattr(value, "isoformat") else value) for name, value in row.items() if name != "source_row"}


def _group_partitions(rows: list[dict[str, Any]], asset_type: str) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        date = str(row["timestamp"])[:10]
        grouped[(date, row["symbol"], asset_type)].append(row)
    return grouped


def _write_partition(lake_root: Path, asset: str, date: str, symbol: str, bronze: list[dict[str, Any]], silver: list[dict[str, Any]], fingerprint: str, snapshot_id: str, diagnostics: dict[str, Any]) -> dict[str, Any]:
    base = lake_root / "partitions" / f"asset_type={asset}" / f"trading_date={date}" / f"symbol={symbol}"
    manifest_path = base / "manifest.json"
    old = _read_json(manifest_path)
    if old and old.get("completed") and old.get("fingerprint") == fingerprint and (base / "bronze.parquet").exists() and (base / "silver.parquet").exists():
        return {"path": str(manifest_path), "reused": True}
    base.mkdir(parents=True, exist_ok=True)
    _atomic_parquet(base / "bronze.parquet", bronze)
    _atomic_parquet(base / "silver.parquet", silver)
    manifest = {"format": LAKE_FORMAT, "completed": True, "fingerprint": fingerprint, "snapshot_id": snapshot_id, "validation_contract": VALIDATION_CONTRACT, "implementation_version": IMPLEMENTATION_VERSION, "quarantine_policy": QUARANTINE_POLICY, "asset_type": asset, "trading_date": date, "symbol": symbol, "bronze_rows": len(bronze), "silver_rows": len(silver), "quality": diagnostics}
    _atomic_json(manifest_path, manifest)
    return {"path": str(manifest_path), "reused": False}


def _partition_fingerprint(snapshot_id: str, source: dict[str, Any], date: str, asset: str) -> str:
    return _sha256_json({"snapshot_id": snapshot_id, "source_sha256": source["sha256"], "date": date, "asset": asset, "validation_contract": VALIDATION_CONTRACT, "implementation_version": IMPLEMENTATION_VERSION})


def _atomic_parquet(path: Path, rows: list[dict[str, Any]]) -> None:
    # Empty silver partitions are still represented by an empty typed table.
    columns = ["symbol", "timestamp", "open", "high", "low", "close", "volume"]
    temporary = path.with_suffix(path.suffix + ".tmp")
    data = [_parquet_row(row) for row in rows]
    try:
        import pandas as pd

        frame = pd.DataFrame(data, columns=columns)
        for column in ("open", "high", "low", "close", "volume"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame.to_parquet(temporary, compression="zstd", index=False)
    except ImportError:
        import polars as pl

        schema = {"symbol": pl.String, "timestamp": pl.String, "open": pl.Float64, "high": pl.Float64, "low": pl.Float64, "close": pl.Float64, "volume": pl.Float64}
        pl.DataFrame(data, schema=schema).write_parquet(temporary, compression="zstd")
    os.replace(temporary, path)


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_json(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def build_market_lake(source: Path, lake_root: Path, layer: str = "bronze") -> LakeBuildResult:
    """Legacy normalized-CSV writer retained for existing users."""
    try:
        import pandas as pd

        frame = pd.read_csv(source)
        timestamp = "timestamp" if "timestamp" in frame.columns else "date"
        if timestamp not in frame.columns or "symbol" not in frame.columns:
            raise ValueError("market lake input requires symbol and timestamp/date columns")
        frame["timestamp"] = frame[timestamp].astype(str)
        frame["trading_date"] = frame["timestamp"].str.slice(0, 10)
        target = lake_root / layer / "market"
        partitions = 0
        for (trading_date, symbol), values in frame.groupby(["trading_date", "symbol"], sort=True):
            output = target / f"trading_date={trading_date}" / f"symbol={symbol}" / "part-000.parquet"
            output.parent.mkdir(parents=True, exist_ok=True)
            values.to_parquet(output, compression="zstd", index=False)
            partitions += 1
        row_count = len(frame)
    except ImportError:
        import polars as pl

        frame = pl.scan_csv(source, try_parse_dates=True).collect(engine="streaming")
        timestamp = "timestamp" if "timestamp" in frame.columns else "date"
        if timestamp not in frame.columns or "symbol" not in frame.columns:
            raise ValueError("market lake input requires symbol and timestamp/date columns")
        frame = frame.with_columns(pl.col(timestamp).cast(pl.Utf8).alias("timestamp"), pl.col(timestamp).cast(pl.Utf8).str.slice(0, 10).alias("trading_date"))
        target = lake_root / layer / "market"
        partitions = 0
        for values in frame.partition_by(["trading_date", "symbol"], as_dict=True).values():
            output = target / f"trading_date={values['trading_date'][0]}" / f"symbol={values['symbol'][0]}" / "part-000.parquet"
            output.parent.mkdir(parents=True, exist_ok=True)
            values.write_parquet(output, compression="zstd")
            partitions += 1
        row_count = frame.height
    manifest_path = target / "manifest.json"
    manifest = {"source": str(source), "fingerprint": dataset_fingerprint([source]), "rows": row_count, "partitions": partitions, "layer": layer}
    _atomic_json(manifest_path, manifest)
    return LakeBuildResult(row_count, manifest["partitions"], manifest["fingerprint"], str(manifest_path))


def register_lake(lake_root: Path) -> Path:
    """Register both partition-backed layers without loading their corpus into Python."""
    catalog = lake_root / "meridian.duckdb"
    connection = duckdb.connect(str(catalog))
    for layer in ("bronze", "silver"):
        pattern = str((lake_root / "partitions" / "**" / f"{layer}.parquet").resolve())
        files = list((lake_root / "partitions").glob(f"**/{layer}.parquet"))
        if files:
            query = f"SELECT * FROM read_parquet('{pattern}', hive_partitioning=true)"
        elif layer == "bronze" and list((lake_root / "bronze" / "market").glob("**/*.parquet")):
            legacy = str((lake_root / "bronze" / "market" / "**" / "*.parquet").resolve())
            query = f"SELECT * FROM read_parquet('{legacy}', hive_partitioning=true)"
        else:
            query = "SELECT CAST(NULL AS VARCHAR) AS symbol, CAST(NULL AS VARCHAR) AS timestamp, CAST(NULL AS DOUBLE) AS open, CAST(NULL AS DOUBLE) AS high, CAST(NULL AS DOUBLE) AS low, CAST(NULL AS DOUBLE) AS close, CAST(NULL AS DOUBLE) AS volume WHERE false"
        connection.execute(f"CREATE OR REPLACE VIEW market_{layer} AS {query}")
    connection.close()
    return catalog
