"""Outcome-blind semantic repairs for Sleeve F feature construction.

The incumbent implementation in :mod:`features.sleeve_f_router` is retained
unchanged for attribution and parity.  This module builds its output from that
implementation and appends repaired fields, leaving every incumbent field
unchanged.  Where a repaired field would otherwise collide with an incumbent
name, it is explicitly prefixed with ``repaired_``:
``repaired_consec_bars`` and ``repaired_oi_*``.

``volume_surprise_60d`` is exactly ``log(f_vol / median_baseline)``, where
``median_baseline`` is the median volume at the same minute of day among the
preceding (never current or future) 60 completed sessions.  A row needs at
least 20 valid preceding-session observations at that minute; otherwise it is
``NaN``.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Final

import numpy as np
import pandas as pd

from features.sleeve_f_router import compute_features


REPAIR_CONFIG: Final[dict[str, object]] = {
    "true_gap_pct": {
        "formula": "(first_open - prior_session_last_close) / prior_session_last_close",
        "missing_prior_close": "nan",
    },
    "natr": {"atr_windows_minutes": [5, 15, 30], "formula": "atr_w / f_close"},
    "consec_bars": {"equal_close": "reset_to_zero"},
    "volume_surprise_60d": {
        "baseline": "median_same_minute_of_day_volume",
        "completed_sessions": 60,
        "minimum_valid_prior_sessions": 20,
        "formula": "log(f_vol / median_baseline)",
        "nonpositive_median_baseline": "nan",
    },
    "oi_quadrants": {"change_lag_minutes": 5, "zero_price_or_oi_change": "all_flags_zero"},
}

BASELINE_COLUMNS: Final[list[str]] = [
    "trade_date",
    "minute_of_day",
    "volume_median_baseline_60d",
    "volume_prior_valid_sessions_60d",
]


def repair_config_hash() -> str:
    """Return the SHA-256 digest of the canonical repair configuration."""
    payload = json.dumps(REPAIR_CONFIG, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalise_raw(raw: pd.DataFrame) -> pd.DataFrame:
    """Match the raw schema accepted by ``build_proxy_features`` without mutation."""
    df = raw.copy()
    if "datetime" in df:
        df["datetime"] = pd.to_datetime(df["datetime"])
    elif "date" in df:
        df["datetime"] = pd.to_datetime(df["date"])
    else:
        raise ValueError("raw data requires a date or datetime column")

    df = df.rename(columns={
        "open": "f_open",
        "high": "f_high",
        "low": "f_low",
        "close": "f_close",
    })
    required = {"f_open", "f_high", "f_low", "f_close"}
    missing = sorted(required.difference(df.columns))
    if missing:
        raise ValueError(f"raw data missing required columns: {', '.join(missing)}")
    if "f_vol" not in df:
        df["f_vol"] = 1.0
    if "f_oi" not in df:
        df["f_oi"] = 0.0
    if "s_close" not in df:
        df["s_close"] = df["f_close"]

    df["trade_date"] = df["datetime"].dt.normalize()
    return df.sort_values("datetime").reset_index(drop=True)


def build_minute_volume_baselines(multi_session_df: pd.DataFrame) -> pd.DataFrame:
    """Build causal, same-minute trailing-60-session volume baselines.

    The result is keyed by ``trade_date`` and ``minute_of_day``.  Each row's
    median considers only the prior 60 *completed sessions*, and retains the
    count of valid observations in that fixed session window.  It intentionally
    does not forward-fill a minute across sessions.
    """
    raw = _normalise_raw(multi_session_df)
    if raw.empty:
        return pd.DataFrame(columns=BASELINE_COLUMNS)

    raw["minute_of_day"] = raw["datetime"].dt.hour * 60 + raw["datetime"].dt.minute
    sessions = pd.Index(raw["trade_date"].drop_duplicates().sort_values())
    session_position = {session: position for position, session in enumerate(sessions)}
    rows: list[dict[str, object]] = []

    # Aggregate duplicate bars at a minute deterministically before computing
    # the session-level matched-minute statistic.
    by_minute = raw.groupby(["trade_date", "minute_of_day"], sort=False)["f_vol"].median()
    for minute, volumes in by_minute.groupby(level="minute_of_day", sort=False):
        values_by_session = volumes.droplevel("minute_of_day")
        for session in values_by_session.index:
            position = session_position[session]
            completed_sessions = int(REPAIR_CONFIG["volume_surprise_60d"]["completed_sessions"])
            minimum_valid_sessions = int(REPAIR_CONFIG["volume_surprise_60d"]["minimum_valid_prior_sessions"])
            prior_sessions = sessions[max(0, position - completed_sessions):position]
            prior_values = values_by_session.reindex(prior_sessions).dropna()
            valid_count = len(prior_values)
            median = float(prior_values.median()) if valid_count >= minimum_valid_sessions else np.nan
            rows.append({
                "trade_date": session,
                "minute_of_day": int(minute),
                "volume_median_baseline_60d": median,
                "volume_prior_valid_sessions_60d": valid_count,
            })
    return pd.DataFrame(rows, columns=BASELINE_COLUMNS).sort_values(
        ["trade_date", "minute_of_day"], kind="stable"
    ).reset_index(drop=True)


def _repaired_streak(close: pd.Series) -> np.ndarray:
    streak = np.zeros(len(close), dtype=np.int16)
    for index in range(1, len(close)):
        change = close.iat[index] - close.iat[index - 1]
        if change > 0:
            streak[index] = max(streak[index - 1], 0) + 1
        elif change < 0:
            streak[index] = min(streak[index - 1], 0) - 1
        # Equality deliberately leaves the initialized zero in this position.
    return streak


def _add_repaired_oi_quadrants(df: pd.DataFrame) -> None:
    change_lag = int(REPAIR_CONFIG["oi_quadrants"]["change_lag_minutes"])
    price_change = df["f_close"].diff(change_lag)
    oi_change = df["f_oi"].diff(change_lag)
    nonzero = (price_change != 0) & (oi_change != 0)
    df["repaired_oi_long_buildup"] = (nonzero & (price_change > 0) & (oi_change > 0)).astype(np.int8)
    df["repaired_oi_short_buildup"] = (nonzero & (price_change < 0) & (oi_change > 0)).astype(np.int8)
    df["repaired_oi_short_cover"] = (nonzero & (price_change > 0) & (oi_change < 0)).astype(np.int8)
    df["repaired_oi_long_unwind"] = (nonzero & (price_change < 0) & (oi_change < 0)).astype(np.int8)


def compute_repaired_features(
    day_df: pd.DataFrame,
    trade_date: date,
    prior_close: float | None,
    minute_baselines: pd.DataFrame | None,
) -> pd.DataFrame:
    """Append semantic repairs for one session without changing incumbent fields.

    ``prior_close`` must be the previous session's final bar close; this API
    makes the cross-session dependency explicit.  ``minute_baselines`` is the
    output of :func:`build_minute_volume_baselines`, optionally filtered to the
    current session.
    """
    raw = _normalise_raw(day_df)
    if raw.empty:
        return pd.DataFrame()
    df = compute_features(raw, trade_date)
    df["minute_of_day"] = df["datetime"].dt.hour * 60 + df["datetime"].dt.minute

    valid_prior_close = prior_close is not None and np.isfinite(prior_close) and prior_close != 0
    if valid_prior_close:
        first_open = float(df["f_open"].iat[0])
        df["true_gap_pct"] = (first_open - float(prior_close)) / float(prior_close)
    else:
        df["true_gap_pct"] = np.nan

    for window in REPAIR_CONFIG["natr"]["atr_windows_minutes"]:
        df[f"natr_{window}m"] = df[f"atr_{window}m"] / df["f_close"].replace(0, np.nan)
    df["repaired_consec_bars"] = _repaired_streak(df["f_close"])
    _add_repaired_oi_quadrants(df)

    df["volume_surprise_60d"] = np.nan
    if minute_baselines is not None and not minute_baselines.empty:
        required = {"minute_of_day", "volume_median_baseline_60d"}
        missing = required.difference(minute_baselines.columns)
        if missing:
            raise ValueError(f"minute_baselines missing required columns: {', '.join(sorted(missing))}")
        baselines = minute_baselines.copy()
        if "trade_date" in baselines:
            session = pd.Timestamp(trade_date).normalize()
            baselines = baselines[pd.to_datetime(baselines["trade_date"]).dt.normalize() == session]
        baseline_by_minute = baselines.drop_duplicates("minute_of_day").set_index("minute_of_day")[
            "volume_median_baseline_60d"
        ]
        median_baseline = df["minute_of_day"].map(baseline_by_minute).where(lambda value: value > 0)
        with np.errstate(divide="ignore", invalid="ignore"):
            df["volume_surprise_60d"] = np.log(df["f_vol"] / median_baseline)
    return df


def repaired_features_multi(raw_multi_session_df: pd.DataFrame) -> pd.DataFrame:
    """Build repaired feature frames for all sessions in chronological order."""
    raw = _normalise_raw(raw_multi_session_df)
    if raw.empty:
        return pd.DataFrame()
    baselines = build_minute_volume_baselines(raw)
    frames: list[pd.DataFrame] = []
    prior_close: float | None = None
    for trade_date, day_df in raw.groupby("trade_date", sort=True):
        day_baselines = baselines[baselines["trade_date"] == trade_date]
        frames.append(compute_repaired_features(day_df, trade_date.date(), prior_close, day_baselines))
        prior_close = float(day_df["f_close"].iloc[-1])
    return pd.concat(frames, ignore_index=True)
