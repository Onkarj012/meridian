"""Frozen, locally-derived research universe manifests.

Universe construction deliberately reads the canonical source snapshot rather
than discovering files itself.  This makes the snapshot the market-data
identity carried into every tradable-symbol decision.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    import polars as pl
except ModuleNotFoundError:  # pragma: no cover - exercised when optional lake deps are absent
    pl = None

from contracts.constituents import constituent_manifest, filter_tradable_rows, load_constituent_history, tradable_on
from lake.io import parse_simple_yaml
from lake.market import normalize_row
from contracts.snapshot import SNAPSHOT_FORMAT, build_source_snapshot


MANIFEST_FORMAT = "meridian.frozen-universe-manifest.v1"
INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "^NSEI", "^NSEBANK"}
_INDEX_ALIASES = {
    "NIFTY 50": "NIFTY",
    "NIFTY": "NIFTY",
    "NIFTY BANK": "BANKNIFTY",
    "BANKNIFTY": "BANKNIFTY",
}


def build_universe(
    source_root: Path,
    output: Path,
    metadata_path: Path = Path("configs/universe.yaml"),
    start: str = "2020-01-01",
    end: str = "2022-12-31",
    size: int = 100,
    min_coverage: float = 0.90,
    metadata_version: str = "v1",
    source_snapshot: Path | None = None,
) -> dict[str, object]:
    """Freeze a liquid universe from snapshot-selected local equity feeds.

    ``source_snapshot`` is the normal downstream seam.  The optional fallback
    builds the canonical snapshot first for compatibility with the original
    CLI; it still never scans uncontracted files directly.
    """
    if size <= 0:
        raise ValueError("universe size must be positive")
    if not 0 < min_coverage <= 1:
        raise ValueError("min_coverage must be within (0, 1]")
    if start > end:
        raise ValueError("selection start must not be after end")
    source_root = source_root.resolve()
    snapshot, snapshot_path = _load_snapshot(source_root, output, source_snapshot)
    metadata_sha256 = _sha256_file(metadata_path)
    sectors = load_sector_map(metadata_path)
    sector_indices = load_sector_index_map(metadata_path)

    values = _daily_traded_values(source_root, snapshot, start, end)
    if not values:
        raise ValueError("source snapshot contains no selected equity candles in selection window")
    max_observed_dates = max(len(days) for days in values.values())
    eligible = [
        (symbol, sum(days.values()), len(days) / max_observed_dates, len(days))
        for symbol, days in values.items()
        if symbol not in INDEX_SYMBOLS and len(days) / max_observed_dates >= min_coverage
    ]
    ranked = sorted(eligible, key=lambda item: (-item[1], item[0]))[:size]
    if len(ranked) < size:
        raise ValueError(f"only {len(ranked)} symbols meet min_coverage={min_coverage}; need {size}")

    selected_symbols = [symbol for symbol, _, _, _ in ranked]
    _validate_metadata(selected_symbols, sectors, sector_indices)
    available_indices = _available_index_feeds(snapshot)
    symbol_rows = [
        _manifest_symbol(symbol, value, coverage, observed_dates, sectors, sector_indices, available_indices)
        for symbol, value, coverage, observed_dates in ranked
    ]
    selection_cutoff = {
        "start": start,
        "end": end,
        "min_coverage": min_coverage,
        "ranking": "aggregated_daily_traded_value_desc_then_symbol_asc",
        "observed_date_denominator": max_observed_dates,
    }
    identity = {
        "format": MANIFEST_FORMAT,
        "kind": f"liquid-{size}",
        "source_snapshot_id": snapshot["snapshot_id"],
        "metadata_version": metadata_version,
        "metadata_sha256": metadata_sha256,
        "selection_cutoff": selection_cutoff,
        "symbols": symbol_rows,
    }
    manifest_id = _sha256_json(identity)
    manifest = {**identity, "manifest_id": manifest_id, "source_snapshot": str(snapshot_path)}
    immutable_path = output.parent / "universe-manifests" / f"{manifest_id}.json"
    _write_immutable_json(immutable_path, manifest)
    _write_output_alias(output, manifest)
    return {
        "manifest_id": manifest_id,
        "manifest": str(immutable_path),
        "output": str(output),
        "source_snapshot_id": snapshot["snapshot_id"],
        "symbols": len(symbol_rows),
        "selection_cutoff": selection_cutoff,
    }


def derive_price_floored_universe(
    base_manifest: Path,
    lake_root: Path,
    output: Path,
    *,
    min_avg_price: float,
    price_window: tuple[str, str] | None = None,
) -> dict[str, object]:
    """Derive a price-floored subset of an existing frozen universe.

    A liquid-by-traded-value universe can admit a sub-rupee penny name whose
    intraday short economics are an artifact (tick granularity + structural
    decline make a downside barrier trivially reachable) and whose real-world
    borrow is impractical. This filter drops members whose average observed
    close over the base selection window is below ``min_avg_price``. The result
    is always a subset of the base universe, so the existing lake is reused and
    no re-selection from raw source is required. Provenance records the base
    manifest id, the floor, and every symbol's observed average close.
    """
    if min_avg_price <= 0:
        raise ValueError("min_avg_price must be positive")
    base = json.loads(Path(base_manifest).read_text(encoding="utf-8"))
    if base.get("format") != MANIFEST_FORMAT:
        raise ValueError(f"not a frozen universe manifest: {base_manifest}")
    cutoff = base.get("selection_cutoff", {})
    start, end = price_window or (str(cutoff.get("start")), str(cutoff.get("end")))
    base_symbols = {str(item["symbol"]).upper() for item in base["symbols"]}
    prices = _lake_average_close(Path(lake_root), base_symbols, start, end)
    identity, dropped = _apply_price_floor(base, prices, float(min_avg_price), [start, end])
    if not identity["symbols"]:
        raise ValueError("price floor removed every universe member")
    manifest_id = _sha256_json(identity)
    manifest = {**identity, "manifest_id": manifest_id, "source_snapshot": base.get("source_snapshot")}
    immutable_path = output.parent / "universe-manifests" / f"{manifest_id}.json"
    _write_immutable_json(immutable_path, manifest)
    _write_output_alias(output, manifest)
    return {
        "manifest_id": manifest_id,
        "manifest": str(immutable_path),
        "output": str(output),
        "kept_symbols": len(identity["symbols"]),
        "dropped_symbols": [{"symbol": symbol, "avg_close": price} for symbol, price in dropped],
        "min_avg_price": float(min_avg_price),
    }


def load_pit_universe(source: Path | str, *, index_name: str | None = None) -> list[dict[str, Any]]:
    """Load point-in-time index membership rows for universe filtering."""
    return load_constituent_history(source, index_name=index_name)


def symbol_in_pit_universe(history: list[dict[str, Any]], symbol: str, as_of: str, *, index_name: str | None = None) -> bool:
    """Compatibility wrapper for PIT constituent membership checks."""
    return tradable_on(history, symbol, as_of, index_name=index_name)


def filter_pit_universe_rows(
    rows: list[dict[str, Any]],
    history: list[dict[str, Any]],
    *,
    date_column: str = "timestamp",
    symbol_column: str = "symbol",
    index_name: str | None = None,
) -> list[dict[str, Any]]:
    """Filter rows to symbols that were PIT constituents on each row date."""
    return filter_tradable_rows(rows, history, date_column=date_column, symbol_column=symbol_column, index_name=index_name)


def pit_universe_manifest(history: list[dict[str, Any]], *, source: Path | str | None = None) -> dict[str, Any]:
    """Return a provenance manifest for PIT universe membership history."""
    return constituent_manifest(history, source=source)


def load_sector_map(metadata_path: Path) -> dict[str, str]:
    if not metadata_path.exists():
        return {}
    config = parse_simple_yaml(metadata_path)
    raw = config.get("sector_by_symbol") or config.get("sectors") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(symbol).upper(): str(sector) for symbol, sector in raw.items()}


def load_sector_index_map(metadata_path: Path) -> dict[str, str]:
    if not metadata_path.exists():
        return {}
    config = parse_simple_yaml(metadata_path)
    raw = config.get("sector_index_by_sector") or config.get("sector_indices") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(sector): str(index_name) for sector, index_name in raw.items()}


def _apply_price_floor(base: dict[str, Any], prices: dict[str, float], min_avg_price: float, window: list[str]) -> tuple[dict[str, Any], list[tuple[str, float]]]:
    kept, dropped = [], []
    for row in base["symbols"]:
        symbol = str(row["symbol"]).upper()
        if symbol not in prices:
            raise ValueError(f"no lake price observed for universe member: {symbol}")
        avg_close = float(prices[symbol])
        if avg_close >= min_avg_price:
            kept.append({**row, "observed_avg_close": avg_close})
        else:
            dropped.append((symbol, avg_close))
    identity = {
        "format": MANIFEST_FORMAT,
        "kind": f"{base.get('kind')}-pricefloor",
        "source_snapshot_id": base.get("source_snapshot_id"),
        "metadata_version": base.get("metadata_version"),
        "metadata_sha256": base.get("metadata_sha256"),
        "selection_cutoff": {**base.get("selection_cutoff", {}), "min_avg_price": min_avg_price, "price_window": window, "derived_from_manifest_id": base.get("manifest_id")},
        "symbols": kept,
    }
    return identity, dropped


def _lake_average_close(lake_root: Path, symbols: set[str], start: str, end: str) -> dict[str, float]:
    _require_polars()
    pattern = str(lake_root / "partitions" / "asset_type=equity" / "**" / "silver.parquet")
    frame = (
        pl.scan_parquet(pattern, hive_partitioning=True)
        .filter(pl.col("symbol").str.to_uppercase().is_in(sorted(symbols)))
        .filter(pl.col("timestamp").str.slice(0, 10).is_between(pl.lit(start), pl.lit(end), closed="both"))
        .group_by(pl.col("symbol").str.to_uppercase().alias("symbol"))
        .agg(pl.col("close").cast(pl.Float64).mean().alias("avg_close"))
        .collect(engine="streaming")
    )
    return {str(row["symbol"]): float(row["avg_close"]) for row in frame.to_dicts()}


def _load_snapshot(source_root: Path, output: Path, source_snapshot: Path | None) -> tuple[dict[str, Any], Path]:
    if source_snapshot is None:
        result = build_source_snapshot(source_root, output.parent / "source-contracts")
        source_snapshot = Path(str(result["snapshot"]))
    if not source_snapshot.is_file():
        raise ValueError(f"source snapshot does not exist: {source_snapshot}")
    try:
        snapshot = json.loads(source_snapshot.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid source snapshot: {source_snapshot}") from exc
    if snapshot.get("format") != SNAPSHOT_FORMAT or not snapshot.get("snapshot_id"):
        raise ValueError(f"not a canonical market source snapshot: {source_snapshot}")
    return snapshot, source_snapshot


def _daily_traded_values(source_root: Path, snapshot: dict[str, Any], start: str, end: str) -> dict[str, dict[str, float]]:
    _require_polars()
    values: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    sources = snapshot.get("sources")
    if not isinstance(sources, list):
        raise ValueError("source snapshot has no sources list")
    for record in sources:
        if not isinstance(record, dict) or record.get("logical_role") not in {"equity_minute", "equity_minute_corrective"}:
            continue
        if record.get("selection_decision") not in {"selected_equity", "selected_corrected_equity"}:
            continue
        symbol = str(record.get("symbol", "")).upper()
        if not symbol or symbol in INDEX_SYMBOLS:
            continue
        relative_path = record.get("path")
        if not isinstance(relative_path, str):
            raise ValueError(f"selected equity {symbol} has no source path in snapshot")
        path = source_root / relative_path
        if not path.is_file():
            raise ValueError(f"selected equity source is missing: {relative_path}")
        if record.get("sha256") != _sha256_file(path):
            raise ValueError(f"selected equity source changed since snapshot: {relative_path}")
        columns = [str(column) for column in record.get("schema", {}).get("columns", [])]
        canonical_to_source = {
            canonical: source
            for source in columns
            for canonical in normalize_row({source: ""})
        }
        timestamp_column = canonical_to_source.get("timestamp")
        close_column = canonical_to_source.get("close")
        volume_column = canonical_to_source.get("volume")
        if not timestamp_column or not close_column or not volume_column:
            raise ValueError(f"snapshot schema lacks timestamp, close, or volume: {relative_path}")
        # CSV rows are never materialized in Python.  The full source hash above
        # remains the trust boundary; this aggregate is only the deterministic
        # liquid-universe ranking calculation.
        daily = (
            pl.scan_csv(path, try_parse_dates=False)
            .select(
                pl.col(timestamp_column).cast(pl.String).str.slice(0, 10).alias("day"),
                pl.col(close_column).cast(pl.Float64).alias("close"),
                pl.col(volume_column).cast(pl.Float64).alias("volume"),
            )
            .filter(pl.col("day").is_between(pl.lit(start), pl.lit(end), closed="both"))
            .group_by("day")
            .agg((pl.col("close") * pl.col("volume")).sum().alias("traded_value"))
            .collect(engine="streaming")
        )
        for day, traded_value in daily.iter_rows():
            values[symbol][str(day)] += float(traded_value)
    return values


def _validate_metadata(symbols: list[str], sectors: dict[str, str], sector_indices: dict[str, str]) -> None:
    missing_sector = [symbol for symbol in symbols if not sectors.get(symbol)]
    if missing_sector:
        raise ValueError(f"frozen universe has unmapped sectors: {', '.join(missing_sector)}")
    missing_index = [symbol for symbol in symbols if not sector_indices.get(sectors[symbol])]
    if missing_index:
        raise ValueError(f"frozen universe has missing sector-index metadata: {', '.join(missing_index)}")


def _available_index_feeds(snapshot: dict[str, Any]) -> set[str]:
    sources = snapshot.get("sources", [])
    return {
        str(item.get("symbol")).upper()
        for item in sources
        if isinstance(item, dict)
        and item.get("logical_role") == "index_minute_context"
        and item.get("selection_decision") == "selected_index_context"
    }


def _manifest_symbol(
    symbol: str,
    value: float,
    coverage: float,
    observed_dates: int,
    sectors: dict[str, str],
    sector_indices: dict[str, str],
    available_indices: set[str],
) -> dict[str, object]:
    sector = sectors[symbol]
    declared_index = sector_indices[sector]
    source_symbol = _INDEX_ALIASES.get(declared_index.upper())
    available = source_symbol in available_indices if source_symbol else False
    return {
        "symbol": symbol,
        "sector": sector,
        "sector_index": declared_index,
        "sector_index_feed": {
            "declared_symbol": declared_index,
            "source_symbol": source_symbol,
            "available": available,
            "context_status": "available_context" if available else "unavailable_context_not_promotable",
        },
        # Kept for compatibility; it is the aggregation used for ranking.
        "daily_traded_value": value,
        "aggregated_daily_traded_value": value,
        "average_observed_daily_traded_value": value / observed_dates,
        "coverage": coverage,
        "observed_dates": observed_dates,
    }


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
            raise RuntimeError(f"immutable universe manifest collision: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded, encoding="utf-8")


def _write_output_alias(path: Path, value: object) -> None:
    """Keep the explicit CLI output usable while immutable versions accumulate."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _require_polars() -> None:
    if pl is None:
        raise ModuleNotFoundError("polars is required for liquid universe lake/CSV aggregation")
