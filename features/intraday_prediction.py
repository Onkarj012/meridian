"""Causal V0 feature matrix for the intraday prediction experiment."""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd


REGIME_CATEGORIES = ("range", "compression", "expansion", "trend_up", "trend_dn", "ineligible")
OPENING_RANGE_COLUMNS = ("or_dist_high", "or_dist_low", "or_breakout_up", "or_breakout_dn")
FEATURE_MANIFEST = [
    "log_ret_1m", "log_ret_5m", "log_ret_15m", "log_ret_30m", "log_ret_60m",
    "realized_vol_5m", "realized_vol_15m", "realized_vol_30m",
    "atr_5m_norm", "atr_15m_norm", "atr_30m_norm",
    "vwap_dev", "vwap_slope_5m", *OPENING_RANGE_COLUMNS,
    "oi_chg_1m", "oi_chg_5m", "oi_chg_30m", "oi_long_buildup", "oi_short_buildup",
    "oi_short_cover", "oi_long_unwind", "vol_zscore", "vol_oi_ratio", "vol_oi_ratio_missing",
    "basis", "basis_chg_30m", "ema_slope", "consec_bars", "regime",
    "session_time_sin", "session_time_cos", "minutes_to_close", "day_of_week",
    "bar_body", "bar_range", "close_location", "upper_wick_fraction",
    "lower_wick_fraction", "session_gap",
]
FORBIDDEN_COLUMNS = {
    "entry_price", "exit_price", "exit_reason", "exit_bar_offset", "net_return_bps",
    "gross_return_bps", "gross_return_r", "net_return_r", "mfe_bps", "mae_bps", "cost_bps",
    "decision_close", "hour_of_day", "minute_of_day", "gap_pct", "f_vol", "f_oi",
    "label_end_ts", "is_decision_eligible", "label_window_contiguous", "label_excluded",
    "is_session_eligible", "is_time_eligible", "is_warmup_eligible", "class_a_eligible",
    "regime_eligible", "f_open", "f_high", "f_low", "f_close", "trade_date", "datetime",
}


def build_prediction_features(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Build only the frozen V0 features and return them in manifest order."""
    if not isinstance(df, pd.DataFrame) or df.empty:
        raise ValueError("df must be a non-empty DataFrame")
    required = {"datetime", "f_open", "f_high", "f_low", "f_close"}
    missing = sorted(required.difference(df.columns))
    if missing:
        raise ValueError(f"intraday features require {', '.join(missing)}")
    frame = df.copy()
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="raise")
    if frame["datetime"].duplicated().any():
        raise ValueError("duplicate timestamps are not allowed")
    frame = frame.sort_values("datetime", kind="stable").reset_index(drop=True)
    frame["trade_date"] = _trade_dates(frame)
    close = pd.to_numeric(frame["f_close"], errors="coerce")
    open_ = pd.to_numeric(frame["f_open"], errors="coerce")
    high = pd.to_numeric(frame["f_high"], errors="coerce")
    low = pd.to_numeric(frame["f_low"], errors="coerce")
    for horizon in (1, 5, 15, 30, 60):
        name = f"log_ret_{horizon}m"
        if horizon == 60 or name not in frame:
            frame[name] = _same_session_log_return(close, frame["trade_date"], horizon)
    for name in ("realized_vol_5m", "realized_vol_15m", "realized_vol_30m", "vwap_dev", "vwap_slope_5m",
                 *OPENING_RANGE_COLUMNS, "oi_chg_1m", "oi_chg_5m", "oi_chg_30m", "oi_long_buildup",
                 "oi_short_buildup", "oi_short_cover", "oi_long_unwind", "vol_zscore", "vol_oi_ratio",
                 "basis", "basis_chg_30m", "ema_slope", "consec_bars"):
        if name not in frame:
            frame[name] = np.nan
    if "vol_oi_ratio_missing" not in frame:
        frame["vol_oi_ratio_missing"] = frame["vol_oi_ratio"].isna().astype("int8")
    else:
        frame["vol_oi_ratio_missing"] = pd.to_numeric(frame["vol_oi_ratio_missing"], errors="coerce").fillna(1).astype("int8")
    for window in (5, 15, 30):
        name = f"atr_{window}m_norm"
        source = f"atr_{window}m"
        frame[name] = pd.to_numeric(frame.get(source, np.nan), errors="coerce") / close.replace(0, np.nan)
    frame["session_time_sin"], frame["session_time_cos"], frame["minutes_to_close"], frame["day_of_week"] = _time_features(frame)
    frame["bar_body"] = (close - open_) / open_.replace(0, np.nan)
    frame["bar_range"] = (high - low) / close.replace(0, np.nan)
    epsilon = np.finfo(float).eps
    frame["close_location"] = (close - low) / (high - low).clip(lower=epsilon)
    bar_range = (high - low).clip(lower=epsilon)
    frame["upper_wick_fraction"] = (high - pd.concat([open_, close], axis=1).max(axis=1)) / bar_range
    frame["lower_wick_fraction"] = (pd.concat([open_, close], axis=1).min(axis=1) - low) / bar_range
    frame["session_gap"] = _session_gap(frame)
    if "regime" not in frame:
        frame["regime"] = "ineligible"
    frame["regime"] = pd.Categorical(frame["regime"].where(frame["regime"].isin(REGIME_CATEGORIES), "ineligible"), categories=REGIME_CATEGORIES)
    before_or = _minute_of_day(frame["datetime"]) < 570
    frame.loc[before_or, list(OPENING_RANGE_COLUMNS)] = np.nan
    features = frame.reindex(columns=FEATURE_MANIFEST).copy()
    for column in FEATURE_MANIFEST:
        if column != "regime":
            features[column] = pd.to_numeric(features[column], errors="coerce")
    if set(features.columns) & FORBIDDEN_COLUMNS:
        raise AssertionError("forbidden columns entered intraday feature matrix")
    return features, list(FEATURE_MANIFEST)


def _trade_dates(frame: pd.DataFrame) -> pd.Series:
    if "trade_date" in frame:
        return pd.to_datetime(frame["trade_date"], errors="raise").dt.normalize()
    return frame["datetime"].dt.normalize()


def _minute_of_day(timestamps: pd.Series) -> pd.Series:
    return timestamps.dt.hour * 60 + timestamps.dt.minute


def _same_session_log_return(close: pd.Series, dates: pd.Series, horizon: int) -> pd.Series:
    values = close.groupby(dates, sort=False).transform(lambda series: np.log(series / series.shift(horizon)))
    return values


def _time_features(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    minute = _minute_of_day(frame["datetime"])
    progress = ((minute - 555) / 375).clip(0, 1)
    angle = 2 * np.pi * progress
    return np.sin(angle), np.cos(angle), 930 - minute, frame["datetime"].dt.dayofweek


def _session_gap(frame: pd.DataFrame) -> pd.Series:
    grouped = frame.groupby("trade_date", sort=False)
    first_open = grouped["f_open"].first()
    last_close = grouped["f_close"].last()
    sessions = pd.Index(first_open.index)
    prior_close = pd.Series(last_close.to_numpy(), index=sessions).shift(1)
    prior_by_date = dict(zip(sessions, prior_close, strict=True))
    return frame["trade_date"].map(prior_by_date).rsub(frame["f_open"]).div(frame["trade_date"].map(prior_by_date))
