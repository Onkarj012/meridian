"""OptiNet source normalization for Meridian.

The helpers in this module are intentionally stdlib-only. They normalize the
historical OptiNet CSV layout into JSON-serializable records without scanning
the large source tree beyond cheap filesystem metadata and small file samples.
"""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, time, timezone, timedelta
from pathlib import Path
from typing import Iterable

IST = timezone(timedelta(hours=5, minutes=30))
TIMEZONE_NAME = "Asia/Kolkata"
SESSION_START = time(9, 15)
SESSION_END = time(15, 30)


def _required(row: dict, field: str) -> str:
    value = row.get(field)
    if value is None or str(value).strip() == "":
        raise ValueError(f"missing required field: {field}")
    return str(value).strip()


def _optional(row: dict, field: str) -> str | None:
    value = row.get(field)
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip()


def _number(value: object, field: str, *, required: bool = True, default: int | float | None = None) -> int | float:
    if value is None or str(value).strip() == "":
        if required:
            raise ValueError(f"missing required numeric field: {field}")
        if default is None:
            raise ValueError(f"missing numeric field: {field}")
        return default
    try:
        numeric = float(str(value).replace(",", "").strip())
    except ValueError as exc:
        raise ValueError(f"invalid numeric field {field}: {value!r}") from exc
    if numeric.is_integer():
        return int(numeric)
    return numeric


def _positive_number(value: object, field: str) -> int | float:
    numeric = _number(value, field)
    if numeric <= 0:
        raise ValueError(f"{field} must be > 0")
    return numeric


def _non_negative_number(value: object, field: str) -> int | float:
    numeric = _number(value, field)
    if not numeric >= 0:
        raise ValueError(f"{field} must be >= 0")
    return numeric


def _parse_datetime(date_value: object, time_value: object | None = None) -> datetime:
    date_text = str(date_value or "").strip()
    time_text = str(time_value or "").strip()
    if not date_text:
        raise ValueError("missing required field: date")

    candidates: list[str] = []
    if time_text:
        candidates.extend(
            [
                f"{date_text} {time_text}",
                f"{date_text}T{time_text}",
            ]
        )
    candidates.append(date_text)

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
    ]

    for candidate in candidates:
        normalized = candidate.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(normalized)
            return dt.replace(tzinfo=IST) if dt.tzinfo is None else dt.astimezone(IST)
        except ValueError:
            pass
        for fmt in formats:
            try:
                return datetime.strptime(candidate, fmt).replace(tzinfo=IST)
            except ValueError:
                continue
    raise ValueError(f"invalid timestamp: date={date_text!r} time={time_text!r}")


def _iso_timestamp(date_value: object, time_value: object | None = None) -> str:
    return _parse_datetime(date_value, time_value).isoformat()


def _deterministic_token(symbol: str) -> int:
    digest = hashlib.sha256(symbol.encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def normalize_equity_minute_row(row: dict, symbol: str, source_path: str | Path | None = None) -> dict:
    """Normalize one OptiNet nifty500 minute bar."""
    if not symbol or not str(symbol).strip():
        raise ValueError("symbol is required")
    return {
        "symbol": str(symbol).strip(),
        "timestamp": _iso_timestamp(_required(row, "date")),
        "open": _number(row.get("open"), "open"),
        "high": _number(row.get("high"), "high"),
        "low": _number(row.get("low"), "low"),
        "close": _number(row.get("close"), "close"),
        "volume": _number(row.get("volume"), "volume"),
        "source_path": str(source_path) if source_path is not None else None,
        "timezone": TIMEZONE_NAME,
        "adjustment_status": "unknown_optinet_minute",
    }


def normalize_index_futures_minute_row(
    row: dict,
    source_path: str | Path | None = None,
    instrument_token: int | None = None,
) -> dict:
    """Normalize one OptiNet NIFTY futures minute row."""
    tradingsymbol = _required(row, "symbol")
    token = int(instrument_token) if instrument_token is not None else _deterministic_token(tradingsymbol)
    return {
        "symbol": tradingsymbol,
        "tradingsymbol": tradingsymbol,
        "instrument_token": token,
        "instrument_type": "FUTIDX",
        "expiry": "continuous_front_month",
        "timestamp": _iso_timestamp(_required(row, "date"), _required(row, "time")),
        "open": _number(row.get("open"), "open"),
        "high": _number(row.get("high"), "high"),
        "low": _number(row.get("low"), "low"),
        "close": _number(row.get("close"), "close"),
        "oi": _non_negative_number(row.get("oi"), "oi"),
        "volume": _positive_number(row.get("volume"), "volume"),
        "source_path": str(source_path) if source_path is not None else None,
        "timezone": TIMEZONE_NAME,
    }


def normalize_index_spot_minute_row(row: dict, source_path: str | Path | None = None) -> dict:
    """Normalize one OptiNet NIFTY spot/index minute context row."""
    symbol = _optional(row, "symbol") or _optional(row, "tradingsymbol") or "NIFTY"
    timestamp = _iso_timestamp(_required(row, "date"), _optional(row, "time"))
    return {
        "symbol": symbol,
        "tradingsymbol": symbol,
        "instrument_type": "INDEX",
        "timestamp": timestamp,
        "open": _number(row.get("open"), "open"),
        "high": _number(row.get("high"), "high"),
        "low": _number(row.get("low"), "low"),
        "close": _number(row.get("close"), "close"),
        "volume": _number(row.get("volume"), "volume", required=False, default=0),
        "context_only": True,
        "volume_status": "context_only_zero_volume_allowed",
        "source_path": str(source_path) if source_path is not None else None,
        "timezone": TIMEZONE_NAME,
    }


def _iter_csv(path: str | Path, normalizer, *args, limit: int | None = None):
    csv_path = Path(path)
    yielded = 0
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for line_number, row in enumerate(reader, start=2):
            if limit is not None and yielded >= limit:
                break
            try:
                yield normalizer(row, *args, source_path=csv_path)
            except ValueError as exc:
                raise ValueError(f"{csv_path}:{line_number}: {exc}") from exc
            yielded += 1


def _symbol_from_equity_filename(path: Path) -> str:
    stem = path.stem
    return stem[: -len("_minute")] if stem.endswith("_minute") else stem


def iter_equity_minute_file(path: str | Path, limit: int | None = None):
    csv_path = Path(path)
    yield from _iter_csv(csv_path, normalize_equity_minute_row, _symbol_from_equity_filename(csv_path), limit=limit)


def iter_nifty_futures_minute_file(path: str | Path, limit: int | None = None):
    yield from _iter_csv(Path(path), normalize_index_futures_minute_row, limit=limit)


def iter_nifty_spot_minute_file(path: str | Path, limit: int | None = None):
    yield from _iter_csv(Path(path), normalize_index_spot_minute_row, limit=limit)


def _family_summary(root: Path, pattern: str = "**/*") -> dict:
    files = sorted(path for path in root.glob(pattern) if path.is_file()) if root.exists() else []
    total_bytes = sum(path.stat().st_size for path in files)
    return {
        "exists": root.exists(),
        "root": str(root),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "sample_paths": [str(path) for path in files[:5]],
    }


def discover_optinet_sources(data_root: str | Path) -> dict:
    root = Path(data_root)
    families = {
        "nifty500": _family_summary(root / "nifty500", "*_minute.csv"),
        "nifty_fut": _family_summary(root / "option_data" / "nifty_data" / "nifty_fut"),
        "nifty_spot": _family_summary(root / "option_data" / "nifty_data" / "nifty_spot"),
        "bhavcopy_cm": _family_summary(root / "bhavcopy" / "cm", "*.parquet"),
        "bhavcopy_fo": _family_summary(root / "bhavcopy" / "fo", "*.parquet"),
        "sentiment": _family_summary(root / "sentiment"),
        "indices": _family_summary(root / "indices"),
    }
    return {"data_root": str(root), "families": families}


def _last_nonempty_line(path: Path) -> str | None:
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            position = handle.tell()
            chunk = b""
            while position > 0:
                read_size = min(4096, position)
                position -= read_size
                handle.seek(position)
                chunk = handle.read(read_size) + chunk
                lines = [line for line in chunk.splitlines() if line.strip()]
                if len(lines) > 1 or position == 0:
                    return lines[-1].decode("utf-8-sig", errors="replace") if lines else None
    except OSError:
        return None
    return None


def _csv_boundaries(path: Path) -> dict | None:
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            first = next(reader, None)
            fieldnames = reader.fieldnames or []
    except (OSError, csv.Error):
        return None
    if not first:
        return None
    last_line = _last_nonempty_line(path)
    last = None
    if last_line:
        try:
            parsed = next(csv.DictReader([",".join(fieldnames), last_line]))
            last = parsed
        except (StopIteration, csv.Error):
            last = None
    try:
        first_ts = _iso_timestamp(first.get("date"), first.get("time"))
    except ValueError:
        first_ts = None
    try:
        last_ts = _iso_timestamp((last or first).get("date"), (last or first).get("time"))
    except ValueError:
        last_ts = None
    return {"path": str(path), "first_timestamp": first_ts, "last_timestamp": last_ts}


def build_optinet_source_manifest(data_root: str | Path, output_path: str | Path | None = None) -> dict:
    discovery = discover_optinet_sources(data_root)
    families = discovery["families"]
    boundaries: dict[str, list[dict]] = {}
    for name, summary in families.items():
        samples = []
        for sample_path in summary["sample_paths"][:3]:
            path = Path(sample_path)
            if path.suffix.lower() == ".csv":
                boundary = _csv_boundaries(path)
                if boundary:
                    samples.append(boundary)
        boundaries[name] = samples

    manifest = {
        "data_root": discovery["data_root"],
        "generated_at": datetime.now(IST).isoformat(),
        "families": families,
        "selected_families": [name for name, summary in families.items() if summary["file_count"] > 0],
        "timestamp_boundaries": boundaries,
        "total_bytes": sum(summary["total_bytes"] for summary in families.values()),
    }
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    manifest["manifest_id"] = hashlib.sha256(encoded).hexdigest()
    if output_path is not None:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def _as_records(rows: Iterable[dict] | dict | None) -> list[dict]:
    if rows is None:
        return []
    if isinstance(rows, dict):
        keys = list(rows)
        return [dict(zip(keys, values)) for values in zip(*(rows[key] for key in keys))]
    return [dict(row) for row in rows]


def summarize_minute_quality(rows: Iterable[dict] | dict | None) -> dict:
    data = _as_records(rows)
    timestamps = []
    zero_volume_count = 0
    zero_oi_count = 0
    invalid_session_count = 0
    symbols = set()
    for row in data:
        if row.get("symbol") is not None:
            symbols.add(str(row["symbol"]))
        if float(row.get("volume") or 0) == 0:
            zero_volume_count += 1
        if "oi" in row and float(row.get("oi") or 0) == 0:
            zero_oi_count += 1
        timestamp = row.get("timestamp") or row.get("date")
        if timestamp:
            try:
                dt = _parse_datetime(timestamp)
                timestamps.append(dt)
                if dt.timetz().replace(tzinfo=None) < SESSION_START or dt.timetz().replace(tzinfo=None) > SESSION_END:
                    invalid_session_count += 1
            except ValueError:
                invalid_session_count += 1
    timestamps.sort()
    return {
        "row_count": len(data),
        "start": timestamps[0].isoformat() if timestamps else None,
        "end": timestamps[-1].isoformat() if timestamps else None,
        "zero_volume_count": zero_volume_count,
        "zero_volume_share": zero_volume_count / len(data) if data else 0.0,
        "zero_oi_count": zero_oi_count,
        "zero_oi_share": zero_oi_count / len(data) if data else 0.0,
        "invalid_session_count": invalid_session_count,
        "symbols": sorted(symbols),
    }


def validate_for_meridian_futures(rows: Iterable[dict] | dict | None) -> dict:
    data = _as_records(rows)
    errors = []
    for index, row in enumerate(data):
        prefix = f"row {index}"
        if not row.get("timestamp"):
            errors.append(f"{prefix}: missing timestamp")
        if not row.get("tradingsymbol"):
            errors.append(f"{prefix}: missing tradingsymbol")
        if row.get("instrument_type") != "FUTIDX":
            errors.append(f"{prefix}: instrument_type must be FUTIDX")
        try:
            if _number(row.get("oi"), "oi") < 0:
                errors.append(f"{prefix}: oi must be >= 0")
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")
        try:
            if _number(row.get("volume"), "volume") <= 0:
                errors.append(f"{prefix}: volume must be > 0")
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")
    return {"valid": not errors, "row_count": len(data), "errors": errors}
