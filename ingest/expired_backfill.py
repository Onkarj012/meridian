"""Shared planning and reporting helpers for expired-futures CLIs."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from .expired_common import minute_file_path, normalise_token


def date_text(value: Any) -> str:
    return pd.Timestamp(value).date().isoformat()


def load_target_rows(
    calendar_path: str | Path,
    gap_days_path: str | Path | None,
    from_date: str | None,
    to_date: str | None,
    out_root: str | Path,
    *,
    validate_only: bool = False,
) -> list[dict[str, Any]]:
    """Load selected trade days, preferring an explicit gap file when supplied."""
    calendar = pd.read_csv(calendar_path, dtype={"front_instrument_id": str, "next_instrument_id": str})
    calendar["trade_date"] = pd.to_datetime(calendar["trade_date"]).dt.date
    if gap_days_path:
        gaps = pd.read_csv(gap_days_path, dtype={"front_instrument_id": str})
        gaps["trade_date"] = pd.to_datetime(gaps["trade_date"]).dt.date
        rows = gaps.merge(calendar, on="trade_date", how="left", suffixes=("", "_calendar"))
        for column in ("front_instrument_id", "front_expiry"):
            backup = f"{column}_calendar"
            if backup in rows:
                rows[column] = rows[column].fillna(rows[backup])
    else:
        rows = calendar.copy()
    if from_date:
        rows = rows[rows["trade_date"] >= date.fromisoformat(from_date)]
    if to_date:
        rows = rows[rows["trade_date"] <= date.fromisoformat(to_date)]
    result = []
    for row in rows.to_dict("records"):
        if not row.get("front_instrument_id") or not row.get("front_expiry"):
            continue
        row["trade_date"] = date_text(row["trade_date"])
        row["front_expiry"] = date_text(row["front_expiry"])
        row["front_instrument_id"] = normalise_token(row["front_instrument_id"])
        exists = minute_file_path(row["trade_date"], out_root).exists()
        if validate_only or gap_days_path or not exists:
            result.append(row)
    return result


def build_plan(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    plan: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        plan[(row["front_instrument_id"], row["front_expiry"])].append(row)
    return dict(plan)


def write_report(report: dict[str, Any], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")


def print_plan(provider: str, plan: dict[tuple[str, str], list[dict[str, Any]]]) -> None:
    days = sum(len(items) for items in plan.values())
    print(f"{provider} expired futures backfill: {days} days in {len(plan)} contracts")
    for (instrument_id, expiry), rows in sorted(plan.items()):
        print(f"  instrument={instrument_id} expiry={expiry}: {', '.join(row['trade_date'] for row in rows)}")
