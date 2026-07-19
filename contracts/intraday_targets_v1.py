"""V1 close-to-close targets with a causal normalized move-size contract."""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from contracts.intraday_targets import (
    DEFAULT_BAND_FLOOR,
    DEFAULT_BAND_MULT,
    DEFAULT_HORIZONS,
    make_targets as make_v0_targets,
)


def make_targets(
    df: pd.DataFrame,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
    band_floor: float = DEFAULT_BAND_FLOOR,
    band_mult: float = DEFAULT_BAND_MULT,
) -> pd.DataFrame:
    """Append the frozen V0 direction and V1 normalized magnitude targets.

    Future prices are looked up by exact timestamp within the same normalized
    trade date.  Invalid rows remain in the returned frame with validity
    flags and NaN future-dependent target values.
    """
    result = make_v0_targets(df, horizons, band_floor=band_floor, band_mult=band_mult)
    for horizon in _horizons(horizons):
        return_bps = result[f"target_h{horizon}_return"] * 10_000
        vol_30 = pd.to_numeric(result["realized_vol_30m"], errors="coerce")
        sigma_h = (vol_30 / np.sqrt(252 * 375)) * np.sqrt(horizon)
        scale = pd.Series(np.maximum(2.0, 10_000 * sigma_h), index=result.index, dtype="float64")
        normalized = return_bps.abs() / scale
        normalized = normalized.where(result[f"target_h{horizon}_valid"] & return_bps.notna())
        result[f"target_h{horizon}_return_bps"] = return_bps
        result[f"target_h{horizon}_scale"] = scale
        result[f"target_h{horizon}_normalized_magnitude"] = normalized
    return result


def make_targets_v1(
    df: pd.DataFrame,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
    band_floor: float = DEFAULT_BAND_FLOOR,
    band_mult: float = DEFAULT_BAND_MULT,
) -> pd.DataFrame:
    """Alias for :func:`make_targets` with an explicit V1 name."""
    return make_targets(df, horizons, band_floor=band_floor, band_mult=band_mult)


def _horizons(horizons: Iterable[int]) -> tuple[int, ...]:
    values = tuple(int(value) for value in horizons)
    if not values or any(value <= 0 for value in values):
        raise ValueError("horizons must contain positive minute values")
    return values
