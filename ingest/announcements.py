from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

import polars as pl


REPORT_FORMAT = "meridian.announcement-feasibility.v1"
CONTRACT_FORMAT = "meridian.announcement-contract.v1"
REQUIRED_FIELDS = ("symbol", "headline", "source", "published_at")
AVAILABILITY_FIELDS = ("available_at", "first_seen_at")
INTRADAY_FORMATS = ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d %H:%M:%S")
ANNOUNCEMENT_NAME_HINTS = ("announcement", "announcements", "corporate", "filing", "filings")
PRUNED_DIR_NAMES = {"partitions", ".git", ".venv", "node_modules", "__pycache__"}
EVENT_TYPE_RULES = {
    "results": ("result", "earnings", "financial results", "quarter"),
    "order_win": ("order", "contract", "loa", "letter of award"),
    "merger_acquisition": ("merger", "acquisition", "amalgamation", "scheme"),
    "pledge": ("pledge", "encumbrance"),
    "rating": ("rating", "credit"),
    "litigation": ("litigation", "court", "tribunal", "legal"),
    "management_change": ("resignation", "appointment", "director", "ceo", "cfo"),
    "dividend": ("dividend",),
    "split_bonus_buyback": ("split", "bonus", "buyback"),
}
IST = timezone(timedelta(hours=5, minutes=30))
NSE_DATE_FORMAT = "%d-%b-%Y %H:%M:%S"
NSE_ANNOUNCEMENTS_URL = "https://www.nseindia.com/api/corporate-announcements"


def build_announcement_feasibility_report(source_roots: Iterable[Path], report_path: Path, *, sample_rows: int = 500) -> dict[str, Any]:
    files = discover_announcement_sources(source_roots)
    inspected = [inspect_announcement_source(path, sample_rows=sample_rows) for path in files]
    usable = [item for item in inspected if item["schema"]["has_required_fields"] and item["timestamp_quality"]["intraday_usable"]]
    report = {
        "format": REPORT_FORMAT,
        "source_roots": [str(path) for path in source_roots],
        "files_discovered": len(files),
        "files_inspected": len(inspected),
        "required_fields": list(REQUIRED_FIELDS),
        "required_availability": "available_at or first_seen_at with timezone-safe intraday timestamps",
        "go": bool(usable),
        "decision": "go" if usable else "no_go",
        "reason": "intraday_available_at_or_first_seen_at_found" if usable else "missing_or_date_only_intraday_availability",
        "coverage": {
            "rows_sampled": sum(int(item["rows_sampled"]) for item in inspected),
            "symbols_sampled": len({symbol for item in inspected for symbol in item["symbols_sampled"]}),
            "usable_files": len(usable),
        },
        "examples": [example for item in inspected for example in item["examples"]][:10],
        "sources": inspected,
    }
    _write_json(Path(report_path), report)
    return report


def build_announcement_contract(source_roots: Iterable[Path], output_path: Path, *, sample_rows: int = 50_000) -> dict[str, Any]:
    files = discover_announcement_sources(source_roots)
    inspected = [inspect_announcement_source(path, sample_rows=min(sample_rows, 500)) for path in files]
    usable_paths = [Path(item["path"]) for item in inspected if item["schema"]["has_required_fields"] and item["timestamp_quality"]["intraday_usable"]]
    rows: list[dict[str, Any]] = []
    for path in usable_paths:
        rows.extend(_contract_rows(path, sample_rows=max(0, sample_rows - len(rows))))
        if len(rows) >= sample_rows:
            break
    rows.sort(key=lambda row: (row["available_at"], row["symbol"], row["event_type"], row["headline"]))
    contract = {
        "format": CONTRACT_FORMAT,
        "source_roots": [str(path) for path in source_roots],
        "source_files": [str(path) for path in usable_paths],
        "decision": "promotable_contract" if rows else "no_go",
        "feature_policy": "event_features_allowed_only_when_available_at_lte_decision_time",
        "required_fields": list(REQUIRED_FIELDS),
        "availability_field_policy": "available_at preferred, first_seen_at fallback",
        "event_type_rules": {key: list(value) for key, value in EVENT_TYPE_RULES.items()},
        "rows": rows,
        "row_count": len(rows),
        "symbols": sorted({row["symbol"] for row in rows}),
        "event_types": sorted({row["event_type"] for row in rows}),
        "sources": inspected,
    }
    _write_json(Path(output_path), contract)
    return {"output": str(output_path), "decision": contract["decision"], "rows": len(rows), "symbols": len(contract["symbols"]), "event_types": contract["event_types"]}


def fetch_nse_corporate_announcements(
    output_path: Path,
    *,
    start: str,
    end: str,
    chunk_days: int = 7,
    pause_seconds: float = 0.15,
) -> dict[str, Any]:
    """Fetch and freeze NSE corporate announcements as normalized PIT rows."""
    output_path = Path(output_path)
    start_day, end_day = datetime.fromisoformat(start).date(), datetime.fromisoformat(end).date()
    if end_day < start_day:
        raise ValueError("end must be on or after start")
    rows: list[dict[str, Any]] = []
    current = start_day
    while current <= end_day:
        chunk_end = min(end_day, current + timedelta(days=max(1, chunk_days) - 1))
        payload = _fetch_nse_chunk(current.strftime("%d-%m-%Y"), chunk_end.strftime("%d-%m-%Y"))
        rows.extend(row for item in payload for row in [_normalize_nse_row(item)] if row is not None)
        current = chunk_end + timedelta(days=1)
        if pause_seconds > 0:
            time.sleep(pause_seconds)
    rows.sort(key=lambda row: (row["available_at"], row["symbol"], row["headline"]))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_csv(output_path)
    return {
        "output": str(output_path),
        "source": "NSE",
        "start": start,
        "end": end,
        "rows": len(rows),
        "symbols": len({row["symbol"] for row in rows}),
    }


def discover_announcement_sources(source_roots: Iterable[Path]) -> list[Path]:
    output: list[Path] = []
    for root in source_roots:
        root = Path(root)
        if not root.exists():
            continue
        for directory, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in PRUNED_DIR_NAMES and not name.startswith("lake-")]
            for filename in filenames:
                path = Path(directory) / filename
                if path.suffix.lower() not in {".csv", ".json", ".jsonl", ".parquet"}:
                    continue
                lowered = path.name.lower()
                if any(hint in lowered for hint in ANNOUNCEMENT_NAME_HINTS):
                    output.append(path)
    return sorted(output)


def inspect_announcement_source(path: Path, *, sample_rows: int = 500) -> dict[str, Any]:
    frame = _read_source_sample(Path(path), sample_rows)
    columns = set(frame.columns)
    missing = [field for field in REQUIRED_FIELDS if field not in columns]
    availability = [field for field in AVAILABILITY_FIELDS if field in columns]
    timestamp_field = "available_at" if "available_at" in availability else "first_seen_at" if "first_seen_at" in availability else None
    timestamp_values = frame.get_column(timestamp_field).to_list() if timestamp_field else []
    timestamp_quality = timestamp_feasibility(timestamp_values)
    symbols = frame.get_column("symbol").cast(pl.String).drop_nulls().unique().to_list() if "symbol" in columns else []
    examples = frame.head(3).to_dicts()
    return {
        "path": str(path),
        "rows_sampled": frame.height,
        "schema": {
            "columns": frame.columns,
            "missing_required_fields": missing,
            "availability_fields": availability,
            "has_required_fields": not missing and bool(availability),
        },
        "timestamp_quality": {
            **timestamp_quality,
            "field": timestamp_field,
        },
        "symbols_sampled": sorted(str(symbol).upper() for symbol in symbols)[:50],
        "examples": [_compact_example(example) for example in examples],
    }


def timestamp_feasibility(values: Iterable[Any]) -> dict[str, Any]:
    sampled = [value for value in values if value not in (None, "")]
    if not sampled:
        return {"intraday_usable": False, "quality": "missing", "sampled": 0, "intraday_count": 0, "date_only_count": 0, "timezone_count": 0}
    intraday = date_only = timezone = 0
    for value in sampled:
        text = str(value)
        parsed = _parse_timestamp(text)
        if len(text.strip()) <= 10:
            date_only += 1
            continue
        if parsed is not None and (parsed.hour, parsed.minute, parsed.second) != (0, 0, 0):
            intraday += 1
        if _has_timezone(text, parsed):
            timezone += 1
    quality = "intraday_timezone_safe" if intraday and timezone == len(sampled) else "intraday_without_timezone" if intraday else "date_only"
    return {
        "intraday_usable": intraday > 0 and timezone == len(sampled),
        "quality": quality,
        "sampled": len(sampled),
        "intraday_count": intraday,
        "date_only_count": date_only,
        "timezone_count": timezone,
    }


def event_rows_available_at(contract_path: Path, decision_time: str) -> list[dict[str, Any]]:
    contract = json.loads(Path(contract_path).read_text(encoding="utf-8"))
    if contract.get("format") != CONTRACT_FORMAT:
        raise ValueError(f"not an announcement contract: {contract_path}")
    decision = _required_timezone_timestamp(decision_time, "decision_time")
    output = []
    for row in contract.get("rows", []):
        available = _required_timezone_timestamp(str(row.get("available_at", "")), "available_at")
        if available <= decision:
            output.append(row)
    return output


def _read_source_sample(path: Path, sample_rows: int) -> pl.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pl.scan_parquet(path).head(sample_rows).collect()
    if suffix == ".csv":
        return pl.read_csv(path, n_rows=sample_rows, infer_schema_length=sample_rows)
    if suffix == ".jsonl":
        return pl.read_ndjson(path, n_rows=sample_rows)
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data if isinstance(data, list) else data.get("rows", data.get("data", [])) if isinstance(data, dict) else []
        return pl.DataFrame(rows[:sample_rows])
    raise ValueError(f"unsupported announcement source format: {path}")


def _contract_rows(path: Path, *, sample_rows: int) -> list[dict[str, Any]]:
    frame = _read_source_sample(path, sample_rows)
    if frame.is_empty():
        return []
    rows = []
    for raw in frame.to_dicts():
        timestamp = raw.get("available_at") or raw.get("first_seen_at")
        quality = timestamp_feasibility([timestamp])
        if not quality["intraday_usable"]:
            continue
        symbol = str(raw.get("symbol", "")).strip().upper()
        headline = str(raw.get("headline", "")).strip()
        source = str(raw.get("source", "")).strip()
        published_at = str(raw.get("published_at", "")).strip()
        available_at = str(timestamp).strip().replace("Z", "+00:00")
        _required_timezone_timestamp(available_at, "available_at")
        if not symbol or not headline or not source or not published_at:
            continue
        rows.append({
            "symbol": symbol,
            "headline": headline,
            "source": source,
            "published_at": published_at,
            "available_at": available_at,
            "event_type": infer_event_type(headline),
            "promotion_status": "point_in_time_promotable",
        })
    return rows


def _fetch_nse_chunk(from_date: str, to_date: str) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"index": "equities", "from_date": from_date, "to_date": to_date})
    request = urllib.request.Request(
        f"{NSE_ANNOUNCEMENTS_URL}?{query}",
        headers={
            "Accept": "application/json",
            "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
            "User-Agent": "Mozilla/5.0",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data if isinstance(data, list) else []


def _normalize_nse_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    symbol = str(raw.get("symbol") or "").strip().upper()
    published = _parse_nse_timestamp(raw.get("an_dt") or raw.get("sort_date"))
    available = _parse_nse_timestamp(raw.get("exchdisstime") or raw.get("an_dt") or raw.get("sort_date"))
    desc = str(raw.get("desc") or "").strip()
    text = str(raw.get("attchmntText") or "").strip()
    headline = " - ".join(part for part in (desc, text) if part)
    if not symbol or not headline or published is None or available is None:
        return None
    return {
        "symbol": symbol,
        "headline": headline,
        "source": "NSE",
        "published_at": published.isoformat(),
        "available_at": available.isoformat(),
        "exchange_seq_id": str(raw.get("seq_id") or ""),
        "event_type_hint": desc,
        "attachment_url": str(raw.get("attchmntFile") or ""),
    }


def _parse_nse_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    for fmt in (NSE_DATE_FORMAT, "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=IST)
        except ValueError:
            continue
    parsed = _parse_timestamp(text)
    return parsed if parsed and parsed.tzinfo else None


def infer_event_type(headline: str) -> str:
    text = headline.lower()
    for event_type, tokens in EVENT_TYPE_RULES.items():
        if any(token in text for token in tokens):
            return event_type
    return "other"


def _parse_timestamp(value: str) -> datetime | None:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass
    for fmt in INTRADAY_FORMATS:
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    return None


def _required_timezone_timestamp(value: str, field: str) -> datetime:
    parsed = _parse_timestamp(value)
    if parsed is None or parsed.tzinfo is None or len(value.strip()) <= 10:
        raise ValueError(f"{field} must be an intraday timezone-aware timestamp")
    return parsed


def _has_timezone(text: str, parsed: datetime | None) -> bool:
    return bool(parsed and parsed.tzinfo) or text.endswith("Z") or "+05:30" in text or text[-6:-5] in {"+", "-"}


def _compact_example(row: dict[str, Any]) -> dict[str, Any]:
    keys = ("symbol", "headline", "source", "published_at", "first_seen_at", "available_at")
    return {key: row.get(key) for key in keys if key in row}


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
