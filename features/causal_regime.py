"""Causal regime classification for deployable Sleeve F candidates."""

from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd


REGIME_CONFIG = {
    "min_valid_sessions": 20,
    "precedence": ["compression", "expansion", "trend_dn", "trend_up"],
    "quantiles": {"rv_p25": 0.25, "rv_p75": 0.75, "rv_p90": 0.90},
    "thresholds": {"ema_slope": 0.001, "ret_30m_abs": 0.001},
    "window_sessions": 60,
}


def regime_config_hash() -> str:
    """Return the hash of the canonical causal-regime configuration."""
    canonical_config = json.dumps(REGIME_CONFIG, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_config.encode("utf-8")).hexdigest()


def add_regime_causal(df: pd.DataFrame) -> pd.DataFrame:
    """Add minute-matched, prior-session-only regime labels to ``df``.

    Each row uses quantiles from its minute of day in the preceding completed
    sessions only. Rows without enough valid observations in that history are
    marked ineligible.
    """
    result = df.copy()
    result["_regime_trade_date"] = pd.to_datetime(result["trade_date"]).dt.normalize()
    session_dates = pd.Index(result["_regime_trade_date"].drop_duplicates()).sort_values()
    result["_regime_session"] = session_dates.get_indexer(result["_regime_trade_date"])

    realized_vol = (
        result.pivot(
            index="_regime_session",
            columns="minute_of_day",
            values="realized_vol_30m",
        )
        .reindex(range(len(session_dates)))
    )
    prior_realized_vol = realized_vol.shift(1)
    window = REGIME_CONFIG["window_sessions"]
    min_valid = REGIME_CONFIG["min_valid_sessions"]
    valid_counts = prior_realized_vol.rolling(window, min_periods=1).count()
    rolling_quantiles = {
        name: prior_realized_vol.rolling(window, min_periods=min_valid).quantile(quantile)
        for name, quantile in REGIME_CONFIG["quantiles"].items()
    }

    sessions = result["_regime_session"].to_numpy()
    minute_columns = realized_vol.columns.get_indexer(result["minute_of_day"])
    if (minute_columns < 0).any():
        raise ValueError("minute_of_day values must be present in the feature frame")

    valid_prior = valid_counts.to_numpy()[sessions, minute_columns] >= min_valid
    threshold_values = {
        name: values.to_numpy()[sessions, minute_columns]
        for name, values in rolling_quantiles.items()
    }
    rv = result["realized_vol_30m"].to_numpy()
    ema_slope = result["ema_slope"].to_numpy()
    ret_30m = result["ret_30m"].to_numpy()
    missing_inputs = np.isnan(rv) | np.isnan(ema_slope) | np.isnan(ret_30m)
    thresholds = REGIME_CONFIG["thresholds"]

    masks = {
        "compression": (rv < threshold_values["rv_p25"]) & (np.abs(ret_30m) < thresholds["ret_30m_abs"]),
        "expansion": rv > threshold_values["rv_p90"],
        "trend_dn": (
            (ema_slope < -thresholds["ema_slope"])
            & (rv < threshold_values["rv_p75"])
            & (ret_30m < 0)
        ),
        "trend_up": (
            (ema_slope > thresholds["ema_slope"])
            & (rv < threshold_values["rv_p75"])
            & (ret_30m > 0)
        ),
    }

    regime = np.full(len(result), "range", dtype=object)
    for label in REGIME_CONFIG["precedence"]:
        regime[masks[label]] = label
    regime[~valid_prior | missing_inputs] = "ineligible"

    result["regime"] = regime
    result["regime_eligible"] = (valid_prior & ~missing_inputs).astype(bool)
    return result.drop(columns=["_regime_trade_date", "_regime_session"])
