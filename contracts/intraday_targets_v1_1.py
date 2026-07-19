"""Forward-only smoothed direction targets for intraday prediction v1.1."""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from contracts.intraday_targets import (
    DEFAULT_BAND_FLOOR,
    DEFAULT_BAND_MULT,
    DEFAULT_HORIZONS,
    CLASS_ORDER,
)
from contracts.intraday_targets import make_targets as make_v0_targets


HALF_LIFE_MINUTES = {15: 5.0, 60: 15.0}
MIN_CLASS_SHARE = 0.15


def make_targets(
    df: pd.DataFrame,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
    band_floor: float = DEFAULT_BAND_FLOOR,
    band_mult: float = DEFAULT_BAND_MULT,
) -> pd.DataFrame:
    """Append raw endpoint and forward-only smoothed direction targets.

    The raw endpoint columns and volatility band come directly from the frozen
    V0 contract.  Smoothed labels use exact, same-session bars from ``t+1``
    through ``t+h`` and retain invalid rows rather than dropping them.
    """
    values = _horizons(horizons)
    result = make_v0_targets(df, values, band_floor=band_floor, band_mult=band_mult)
    result = result.sort_values("datetime", kind="stable").reset_index(drop=True)
    frame = result
    timestamps = pd.to_datetime(frame["datetime"], errors="raise")
    dates = pd.to_datetime(frame["trade_date"], errors="raise").dt.normalize()
    closes = pd.to_numeric(frame["f_close"], errors="coerce")

    for horizon in values:
        half_life = HALF_LIFE_MINUTES.get(horizon)
        if half_life is None:
            raise ValueError(f"no frozen smooth-target half-life for horizon {horizon}")
        alpha = 1.0 - 2.0 ** (-1.0 / half_life)
        smooth_return = pd.Series(np.nan, index=frame.index, dtype="float64")
        valid = np.zeros(len(frame), dtype=bool)
        close_lookup = {
            (timestamp, date): float(close)
            for timestamp, date, close in zip(timestamps, dates, closes, strict=True)
            if pd.notna(close)
        }
        for row, (timestamp, date, close) in enumerate(zip(timestamps, dates, closes, strict=True)):
            if not pd.notna(close) or close <= 0:
                continue
            q = float(close)
            complete = True
            for offset in range(1, horizon + 1):
                future_timestamp = timestamp + pd.Timedelta(minutes=offset)
                future_close = close_lookup.get((future_timestamp, date))
                if future_close is None or future_close <= 0:
                    complete = False
                    break
                q = alpha * future_close + (1.0 - alpha) * q
            if complete and q > 0:
                valid[row] = True
                smooth_return.iloc[row] = np.log(q / float(close))

        band = pd.to_numeric(frame[f"target_h{horizon}_band"], errors="coerce")
        direction = pd.Series(pd.NA, index=frame.index, dtype="string")
        direction.loc[valid & (smooth_return > band).to_numpy()] = "UP"
        direction.loc[valid & (smooth_return < -band).to_numpy()] = "DOWN"
        flat = valid & smooth_return.notna().to_numpy() & ~(smooth_return > band).to_numpy() & ~(smooth_return < -band).to_numpy()
        direction.loc[flat] = "FLAT"

        result[f"target_h{horizon}_smooth_return"] = smooth_return
        result[f"target_h{horizon}_smooth_return_bps"] = smooth_return * 10_000
        result[f"target_h{horizon}_smooth_dir"] = direction
        result[f"target_h{horizon}_smooth_band"] = band.where(valid)
        result[f"target_h{horizon}_smooth_valid"] = valid
        result[f"target_h{horizon}_smooth_end_ts"] = pd.Series(
            np.where(valid, (timestamps + pd.Timedelta(minutes=horizon)).astype("datetime64[ns]"), pd.NaT),
            index=frame.index,
        )
    return result


def class_share_validity(
    labels: Iterable[object],
    *,
    min_share: float = MIN_CLASS_SHARE,
) -> dict[str, object]:
    """Return the frozen structural class-share validity result."""
    values = pd.Series(list(labels), dtype="string").dropna()
    counts = {label: int((values == label).sum()) for label in CLASS_ORDER}
    total = int(len(values))
    shares = {label: (counts[label] / total if total else 0.0) for label in CLASS_ORDER}
    valid = bool(total and all(share >= float(min_share) for share in shares.values()))
    return {
        "valid": valid,
        "structural_failure": not valid,
        "rows": total,
        "counts": counts,
        "shares": shares,
        "min_share": float(min_share),
    }


check_class_share_validity = class_share_validity


def _horizons(horizons: Iterable[int]) -> tuple[int, ...]:
    values = tuple(int(value) for value in horizons)
    if not values or any(value <= 0 for value in values):
        raise ValueError("horizons must contain positive minute values")
    return values
