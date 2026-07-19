"""Fixed close-to-close intraday targets with same-session window guards."""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd


DEFAULT_HORIZONS = (15, 60)
DEFAULT_BAND_FLOOR = 0.0002
DEFAULT_BAND_MULT = 0.25
CLASS_ORDER = ("DOWN", "FLAT", "UP")


def validate_target_windows(
    df: pd.DataFrame,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
) -> pd.DataFrame:
    """Return one same-session, exact-timestamp validity column per horizon.

    A row is valid only when the requested future timestamp exists as an exact
    minute bar and has the same normalized trade date.  The function never
    uses a plain row shift, so missing bars and session boundaries are explicit.
    """
    frame = _normalise_input(df)
    result = pd.DataFrame(index=frame.index)
    timestamps = pd.to_datetime(frame["datetime"])
    dates = _trade_dates(frame)
    for horizon in _horizons(horizons):
        future = timestamps + pd.Timedelta(minutes=horizon)
        lookup = pd.Series(dates.to_numpy(), index=timestamps).to_dict()
        result[f"valid_h{horizon}"] = [
            bool(ts in lookup and lookup[ts] == date)
            for ts, date in zip(future, dates, strict=True)
        ]
    return result


def make_targets(
    df: pd.DataFrame,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
    band_floor: float = DEFAULT_BAND_FLOOR,
    band_mult: float = DEFAULT_BAND_MULT,
) -> pd.DataFrame:
    """Append raw magnitude and volatility-scaled direction targets.

    Target columns are named ``target_h{H}_*``.  The future close is looked up
    by exact timestamp within the same session; invalid windows remain NaN.
    ``realized_vol_30m`` is annualized and converted to horizon return units
    using the frozen 252 x 375 minute convention.
    """
    if band_floor < 0 or band_mult < 0:
        raise ValueError("band_floor and band_mult must be non-negative")
    frame = _normalise_input(df)
    if "f_close" not in frame or "realized_vol_30m" not in frame:
        raise ValueError("targets require f_close and realized_vol_30m")
    result = frame.copy()
    result["datetime"] = pd.to_datetime(result["datetime"])
    dates = _trade_dates(result)
    result["trade_date"] = dates
    windows = validate_target_windows(result, horizons)
    for horizon in _horizons(horizons):
        close_by_timestamp = pd.Series(
            pd.to_numeric(result["f_close"], errors="raise").to_numpy(),
            index=result["datetime"],
        )
        future_ts = result["datetime"] + pd.Timedelta(minutes=horizon)
        future_close = future_ts.map(close_by_timestamp)
        valid = windows[f"valid_h{horizon}"].to_numpy(dtype=bool)
        close = pd.to_numeric(result["f_close"], errors="coerce")
        log_return = pd.Series(np.nan, index=result.index, dtype="float64")
        valid_prices = valid & close.gt(0).to_numpy() & future_close.gt(0).to_numpy()
        log_return.loc[valid_prices] = np.log(
            future_close.loc[valid_prices].to_numpy(dtype=float)
            / close.loc[valid_prices].to_numpy(dtype=float)
        )
        vol_30 = pd.to_numeric(result["realized_vol_30m"], errors="coerce")
        sigma_h = (vol_30 / np.sqrt(252 * 375)) * np.sqrt(horizon)
        band = pd.Series(np.maximum(float(band_floor), float(band_mult) * sigma_h), index=result.index)
        direction = pd.Series(pd.NA, index=result.index, dtype="string")
        direction.loc[log_return > band] = "UP"
        direction.loc[log_return < -band] = "DOWN"
        direction.loc[log_return.notna() & ~(log_return > band) & ~(log_return < -band)] = "FLAT"
        end_ts = pd.Series(pd.NaT, index=result.index, dtype="datetime64[ns]")
        end_ts.loc[valid] = future_ts.loc[valid]
        result[f"target_h{horizon}_return"] = log_return
        result[f"target_h{horizon}_mag_bps"] = log_return * 10_000
        result[f"target_h{horizon}_band"] = band.where(log_return.notna())
        result[f"target_h{horizon}_dir"] = direction
        result[f"target_h{horizon}_end_ts"] = end_ts
        result[f"target_h{horizon}_valid"] = valid
    return result


def _normalise_input(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df, pd.DataFrame) or df.empty:
        raise ValueError("df must be a non-empty DataFrame")
    if "datetime" not in df or "f_close" not in df:
        raise ValueError("df requires datetime and f_close")
    result = df.copy()
    result["datetime"] = pd.to_datetime(result["datetime"], errors="raise")
    if result["datetime"].duplicated().any():
        raise ValueError("duplicate timestamps are not allowed")
    result = result.sort_values("datetime", kind="stable")
    if "trade_date" not in result:
        result["trade_date"] = result["datetime"].dt.normalize()
    else:
        result["trade_date"] = pd.to_datetime(result["trade_date"], errors="raise").dt.normalize()
    return result


def _trade_dates(frame: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(frame["trade_date"], errors="raise").dt.normalize()


def _horizons(horizons: Iterable[int]) -> tuple[int, ...]:
    values = tuple(int(value) for value in horizons)
    if not values or any(value <= 0 for value in values):
        raise ValueError("horizons must contain positive minute values")
    return values
