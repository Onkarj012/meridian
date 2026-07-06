"""Reconciliation-only yfinance fallback boundary."""
from __future__ import annotations

from datetime import date

import pandas as pd


def fetch_daily_fallback(symbols: list[str], start: date | str, end: date | str) -> pd.DataFrame:
    try:
        import yfinance as yf  # type: ignore
    except ImportError as exc:
        raise RuntimeError("yfinance is optional and only used for reconciliation") from exc
    frames: list[pd.DataFrame] = []
    for symbol in symbols:
        raw = yf.download(f"{symbol}.NS", start=start, end=end, progress=False, auto_adjust=False)
        if raw.empty:
            continue
        df = raw.reset_index()
        df.columns = [str(c).lower().replace(" ", "_") for c in df.columns]
        df["symbol"] = symbol
        df["source"] = "yfinance_reconciliation"
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
