"""Quarantined GDELT ingest helpers."""
from __future__ import annotations

import pandas as pd


def fetch_gdelt(records: list[dict] | None = None) -> pd.DataFrame:
    df = pd.DataFrame(records or [])
    if not df.empty:
        df["source"] = "gdelt"
        df["quarantine_status"] = "quarantined"
    return df
