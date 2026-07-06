"""Quarantined news ingest helpers."""
from __future__ import annotations

import pandas as pd


def fetch_news(records: list[dict] | None = None) -> pd.DataFrame:
    df = pd.DataFrame(records or [])
    if not df.empty:
        df["quarantine_status"] = "quarantined"
    return df
