"""Macro context ingest helpers with release timestamps."""
from __future__ import annotations

from datetime import datetime

import pandas as pd


def fetch_macro_series(name: str, values: list[dict] | None = None, released_at: str | datetime | None = None) -> pd.DataFrame:
    df = pd.DataFrame(values or [])
    df["series"] = name
    df["released_at"] = pd.Timestamp(released_at or pd.Timestamp.utcnow()).isoformat()
    return df
