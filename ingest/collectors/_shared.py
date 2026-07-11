"""Small common helpers used by collector modules."""
from __future__ import annotations

import json
from typing import Any, Callable, Iterable, Mapping

from ingest.collector_base import append_observation


BASE_COLUMNS = ["source_ts", "exchange_ts", "receive_ts", "raw"]


def schema(*columns: str) -> list[str]:
    return list(dict.fromkeys([*columns, *BASE_COLUMNS]))


def dry_result(columns: list[str], *, note: str) -> dict[str, Any]:
    return {"action": "dry_run", "schema": columns, "records": [{column: None for column in columns}], "note": note}


def unpack(result: Any) -> tuple[list[dict[str, Any]], Any]:
    """Accept the simple injected fetcher shapes used by all collectors."""
    if isinstance(result, tuple) and len(result) == 2:
        return [dict(row) for row in result[0]], result[1]
    if isinstance(result, Mapping):
        rows = result.get("records", result.get("rows", []))
        return [dict(row) for row in rows], result.get("raw", result)
    return [dict(row) for row in result], result


def persist(
    collector: str, columns: list[str], rows: Iterable[Mapping[str, Any]], raw: Any,
    *, lake_root: str | None = None, partition_by: str | None = None, receive_ts: str | None = None,
) -> dict[str, Any]:
    normalized = []
    for row in rows:
        normalized.append({column: row.get(column) for column in columns if column != "raw"})
    if not normalized:
        return {"action": "no_data", "schema": columns, "records": 0, "note": "fetch_returned_no_rows"}
    partition = None
    if partition_by:
        partition = f"{partition_by}={normalized[0].get(partition_by) or 'unknown'}"
    result = append_observation(
        collector, normalized, raw=raw, lake_root=lake_root, partition=partition, receive_ts=receive_ts,
    )
    result["schema"] = columns
    return result


def raw_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
