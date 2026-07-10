"""Exact feature port for the incumbent Sleeve F LightGBM router.

This module intentionally preserves the original one-day feature semantics from
``intranet_optinet/src/engine/features.py``.  It is not the general-purpose
Sleeve F feature pipeline.
"""
from __future__ import annotations

from datetime import date, time as dtime

import numpy as np
import pandas as pd

MINUTES_PER_SESSION = 375
EPS = 1e-8

FUTURES_FEATURES = [
    "ret_1m", "ret_5m", "ret_15m", "ret_30m", "ret_60m",
    "log_ret_1m", "log_ret_5m", "log_ret_15m", "log_ret_30m",
    "atr_5m", "atr_15m", "atr_30m",
    "realized_vol_5m", "realized_vol_15m", "realized_vol_30m",
    "vwap_dev", "vwap_slope_5m",
    "or_dist_high", "or_dist_low", "or_breakout_up", "or_breakout_dn",
    "oi_chg_1m", "oi_chg_5m", "oi_chg_30m",
    "oi_long_buildup", "oi_short_buildup", "oi_short_cover", "oi_long_unwind",
    "basis", "basis_chg_30m",
    "minute_of_day", "hour_of_day", "session_progress", "day_of_week",
    "gap_pct", "consec_bars", "ema_slope", "vol_oi_ratio", "vol_zscore",
]


def compute_features(df: pd.DataFrame, trade_date: date) -> pd.DataFrame:
    """Compute the incumbent's 39 inputs for one trading day, exactly."""
    df = df.copy().reset_index(drop=True)
    n = len(df)
    if n == 0:
        return pd.DataFrame()

    for lag in (1, 5, 15, 30, 60):
        df[f"ret_{lag}m"] = df["f_close"].pct_change(lag)
        df[f"log_ret_{lag}m"] = np.log(df["f_close"] / df["f_close"].shift(lag))

    df["prev_close"] = df["f_close"].shift(1)
    df["tr"] = np.maximum(
        df["f_high"] - df["f_low"],
        np.maximum(
            (df["f_high"] - df["prev_close"]).abs(),
            (df["f_low"] - df["prev_close"]).abs(),
        ),
    )
    for window in (5, 15, 30):
        df[f"atr_{window}m"] = df["tr"].rolling(window, min_periods=3).mean()
        df[f"realized_vol_{window}m"] = (
            df["log_ret_1m"].rolling(window, min_periods=3).std()
            * np.sqrt(MINUTES_PER_SESSION * 252)
        )

    df["cum_vol"] = df["f_vol"].cumsum()
    df["cum_pv"] = (df["f_close"] * df["f_vol"]).cumsum()
    df["vwap"] = df["cum_pv"] / df["cum_vol"].replace(0, np.nan)
    df["vwap_dev"] = (df["f_close"] - df["vwap"]) / df["vwap"].replace(0, np.nan)
    df["vwap_slope_5m"] = df["vwap"].diff(5) / df["vwap"].shift(5).replace(0, np.nan)

    or_mask = df["datetime"].dt.time < dtime(9, 30)
    or_high = df.loc[or_mask, "f_high"].max() if or_mask.any() else df["f_high"].iloc[0]
    or_low = df.loc[or_mask, "f_low"].min() if or_mask.any() else df["f_low"].iloc[0]
    or_range = max(or_high - or_low, EPS)
    df["or_high"] = or_high
    df["or_low"] = or_low
    df["or_dist_high"] = (df["f_close"] - or_high) / or_range
    df["or_dist_low"] = (df["f_close"] - or_low) / or_range
    df["or_breakout_up"] = (df["f_close"] > or_high).astype(np.int8)
    df["or_breakout_dn"] = (df["f_close"] < or_low).astype(np.int8)

    for lag in (1, 5, 30):
        df[f"oi_chg_{lag}m"] = df["f_oi"].diff(lag) / df["f_oi"].shift(lag).replace(0, np.nan)
    price_up_5 = df["f_close"].diff(5) > 0
    oi_up_5 = df["f_oi"].diff(5) > 0
    df["oi_long_buildup"] = (price_up_5 & oi_up_5).astype(np.int8)
    df["oi_short_buildup"] = (~price_up_5 & oi_up_5).astype(np.int8)
    df["oi_short_cover"] = (price_up_5 & ~oi_up_5).astype(np.int8)
    df["oi_long_unwind"] = (~price_up_5 & ~oi_up_5).astype(np.int8)

    df["basis"] = (df["f_close"] - df["s_close"]) / df["s_close"].replace(0, np.nan)
    df["basis_chg_30m"] = df["basis"].diff(30)

    df["minute_of_day"] = df["datetime"].dt.hour * 60 + df["datetime"].dt.minute
    df["hour_of_day"] = df["datetime"].dt.hour
    df["session_progress"] = ((df["minute_of_day"] - 555) / (930 - 555)).clip(0, 1)
    df["day_of_week"] = df["datetime"].dt.dayofweek

    first_open = float(df["f_open"].iloc[0])
    previous = float(df["prev_close"].iloc[0]) if not np.isnan(df["prev_close"].iloc[0]) else first_open
    df["gap_pct"] = (first_open - previous) / max(abs(previous), EPS)

    df["up_bar"] = (df["f_close"] > df["f_close"].shift(1)).astype(np.int8)
    consec = np.zeros(n, dtype=np.int8)
    for index in range(1, n):
        if df["up_bar"].iat[index] == 1:
            consec[index] = max(consec[index - 1], 0) + 1
        else:
            consec[index] = min(consec[index - 1], 0) - 1
    df["consec_bars"] = consec
    df["ema9"] = df["f_close"].ewm(span=9, adjust=False).mean()
    df["ema21"] = df["f_close"].ewm(span=21, adjust=False).mean()
    df["ema_slope"] = (df["ema9"] - df["ema21"]) / df["ema21"].replace(0, np.nan)

    df["vol_oi_ratio"] = df["f_vol"] / df["f_oi"].replace(0, np.nan)
    vol_mean = df["f_vol"].rolling(30, min_periods=5).mean()
    vol_std = df["f_vol"].rolling(30, min_periods=5).std()
    df["vol_zscore"] = (df["f_vol"] - vol_mean) / vol_std.replace(0, np.nan)
    df["trade_date"] = pd.Timestamp(trade_date)
    return df


def add_regime(df: pd.DataFrame) -> pd.DataFrame:
    """Port of the incumbent compression/regime classifier."""
    rv = df["realized_vol_30m"]
    rv_p25 = rv.quantile(0.25)
    rv_p75 = rv.quantile(0.75)
    rv_p90 = rv.quantile(0.90)
    trend_up = (df["ema_slope"] > 0.001) & (rv < rv_p75) & (df["ret_30m"] > 0)
    trend_dn = (df["ema_slope"] < -0.001) & (rv < rv_p75) & (df["ret_30m"] < 0)
    expansion = rv > rv_p90
    compression = (rv < rv_p25) & (df["ret_30m"].abs() < 0.001)
    regime = pd.Series("range", index=df.index)
    regime[compression] = "compression"
    regime[expansion] = "expansion"
    regime[trend_dn] = "trend_dn"
    regime[trend_up] = "trend_up"
    df = df.copy()
    df["regime"] = regime
    return df


def build_proxy_features(raw: pd.DataFrame) -> pd.DataFrame:
    """Build the incumbent forward-walk NIFTY index proxy feature table."""
    raw = raw.copy()
    raw["datetime"] = pd.to_datetime(raw["date"])
    raw = raw.rename(columns={
        "open": "f_open", "high": "f_high", "low": "f_low", "close": "f_close",
    })
    raw["f_vol"] = 1.0
    raw["f_oi"] = 0.0
    raw["s_close"] = raw["f_close"]
    raw["trade_date"] = raw["datetime"].dt.normalize()
    raw = raw.sort_values("datetime").reset_index(drop=True)

    frames = []
    for trade_date, day_df in raw.groupby("trade_date"):
        if len(day_df) < 60:
            continue
        feats = compute_features(day_df, trade_date.date())
        for column in ("oi_chg_1m", "oi_chg_5m", "oi_chg_30m", "vol_oi_ratio", "vol_zscore"):
            feats[column] = feats[column].fillna(0.0)
        frames.append(feats)
    full = pd.concat(frames, ignore_index=True)
    full["datetime"] = pd.to_datetime(full["datetime"])
    full["trade_date"] = pd.to_datetime(full["trade_date"])
    full = full.dropna(subset=FUTURES_FEATURES)
    return add_regime(full)
