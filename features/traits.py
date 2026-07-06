"""Stock-trait conditioning features.

These are stable stock identity descriptors, not learned embeddings. They are
safe to join as slow-moving context when their source timestamps are point in
time.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd


TRAIT_COLUMNS = (
    "sector",
    "mcap_bucket",
    "beta",
    "volatility",
    "liquidity_bucket",
    "dividend_yield",
    "float_pct",
)


def build_trait_vectors(symbol_metadata: Sequence[Mapping[str, Any]] | pd.DataFrame | None = None) -> dict[str, dict[str, object]]:
    rows = _records(symbol_metadata)
    output: dict[str, dict[str, object]] = {}
    for item in rows:
        symbol = str(item.get("symbol", "")).upper()
        if not symbol:
            continue
        output[symbol] = {
            "sector": str(item.get("sector", "UNKNOWN") or "UNKNOWN"),
            "mcap_bucket": mcap_bucket(item.get("mcap_bucket", item.get("market_cap"))),
            "beta": _float(item.get("beta"), 1.0),
            "volatility": _float(item.get("volatility", item.get("vol")), 0.0),
            "liquidity_bucket": liquidity_bucket(item),
            "dividend_yield": _float(item.get("dividend_yield", item.get("div_yield")), 0.0),
            "float_pct": _float(item.get("float_pct", item.get("free_float_pct", item.get("float"))), 0.0),
        }
    return output


def trait_frame(symbol_metadata: Sequence[Mapping[str, Any]] | pd.DataFrame | None = None) -> pd.DataFrame:
    vectors = build_trait_vectors(symbol_metadata)
    if not vectors:
        return pd.DataFrame(columns=("symbol", *TRAIT_COLUMNS))
    frame = pd.DataFrame.from_dict(vectors, orient="index")
    frame.insert(0, "symbol", frame.index)
    return frame.reset_index(drop=True)


def mcap_bucket(value: Any) -> str:
    if value in (None, ""):
        return "unknown"
    if isinstance(value, str) and not _is_number(value):
        return value.lower()
    market_cap = float(value)
    if market_cap >= 1_000_000_000_000:
        return "mega"
    if market_cap >= 200_000_000_000:
        return "large"
    if market_cap >= 50_000_000_000:
        return "mid"
    if market_cap >= 10_000_000_000:
        return "small"
    return "micro"


def liquidity_bucket(item: Mapping[str, Any]) -> str:
    explicit = item.get("liquidity_bucket")
    if explicit not in (None, ""):
        return str(explicit).lower()
    adv = item.get("adv20", item.get("avg_daily_value", item.get("daily_turnover")))
    if adv in (None, ""):
        return "unknown"
    value = float(adv)
    if value >= 1_000_000_000:
        return "large"
    if value >= 250_000_000:
        return "liquid"
    if value >= 50_000_000:
        return "medium"
    if value >= 10_000_000:
        return "small"
    return "illiquid"


def encode_trait_matrix(traits: Mapping[str, Mapping[str, object]]) -> tuple[list[str], np.ndarray, list[str]]:
    """Return a numeric matrix with one-hot categoricals and numeric traits."""
    symbols = sorted(traits)
    categories = {
        "sector": sorted({str(traits[symbol].get("sector", "UNKNOWN")) for symbol in symbols}),
        "mcap_bucket": sorted({str(traits[symbol].get("mcap_bucket", "unknown")) for symbol in symbols}),
        "liquidity_bucket": sorted({str(traits[symbol].get("liquidity_bucket", "unknown")) for symbol in symbols}),
    }
    columns: list[str] = []
    for key, values in categories.items():
        columns.extend(f"{key}={value}" for value in values)
    columns.extend(["beta", "volatility", "dividend_yield", "float_pct"])

    matrix = np.zeros((len(symbols), len(columns)), dtype=float)
    col_index = {name: index for index, name in enumerate(columns)}
    for row_index, symbol in enumerate(symbols):
        row = traits[symbol]
        for key in categories:
            matrix[row_index, col_index[f"{key}={row.get(key, 'unknown')}"]] = 1.0
        for key in ("beta", "volatility", "dividend_yield", "float_pct"):
            matrix[row_index, col_index[key]] = _float(row.get(key), 0.0)
    return symbols, matrix, columns


def _records(values: Sequence[Mapping[str, Any]] | pd.DataFrame | None) -> list[Mapping[str, Any]]:
    if values is None:
        return []
    if isinstance(values, pd.DataFrame):
        return values.to_dict("records")
    return list(values)


def _float(value: Any, default: float) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False
