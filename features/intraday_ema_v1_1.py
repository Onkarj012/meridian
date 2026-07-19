"""Fixed causal EMA features and S-ladder manifests for intraday v1.1."""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from features.intraday_prediction import FEATURE_MANIFEST as V0_FEATURE_MANIFEST
from features.intraday_prediction_v1 import (
    assert_no_forbidden_columns,
    build_prediction_features_v1,
)


EMA_FEATURE_COLUMNS = [
    "price_to_ema_5",
    "price_to_ema_15",
    "ema_5_slope_5m",
    "ema_15_slope_5m",
    "ema_5_minus_ema_15",
]
EMA_WARMUP_COLUMN = "ema_warmup"
S0_FEATURE_MANIFEST = list(V0_FEATURE_MANIFEST)
S1_FEATURE_MANIFEST = S0_FEATURE_MANIFEST + list(EMA_FEATURE_COLUMNS)
S2_FEATURE_MANIFEST = list(S0_FEATURE_MANIFEST)
S3_FEATURE_MANIFEST = list(S1_FEATURE_MANIFEST)
FEATURE_MANIFESTS = {"S0": S0_FEATURE_MANIFEST, "S1": S1_FEATURE_MANIFEST, "S2": S2_FEATURE_MANIFEST, "S3": S3_FEATURE_MANIFEST}
CANDIDATE_FEATURES = FEATURE_MANIFESTS
FEATURE_MANIFEST = S3_FEATURE_MANIFEST

assert len(S0_FEATURE_MANIFEST) == 42
assert S1_FEATURE_MANIFEST == S0_FEATURE_MANIFEST + EMA_FEATURE_COLUMNS
assert S2_FEATURE_MANIFEST == S0_FEATURE_MANIFEST
assert S3_FEATURE_MANIFEST == S1_FEATURE_MANIFEST


def build_ema_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build the five same-session EMA features available at decision time.

    The returned frame contains exactly the five model features.  The
    companion warm-up flag is available through :func:`ema_warmup_flag` and
    is also stored in ``DataFrame.attrs`` for callers that need eligibility
    metadata without adding a sixth model input.
    """
    frame, dates, close = _normalise_input(df)
    result = pd.DataFrame(index=frame.index, columns=EMA_FEATURE_COLUMNS, dtype="float64")
    warmup = ema_warmup_flag(frame)
    for date, positions in frame.groupby("trade_date", sort=False).groups.items():
        del date
        index = pd.Index(positions)
        prices = close.loc[index]
        ema5 = prices.ewm(alpha=_alpha(5), adjust=False).mean()
        ema15 = prices.ewm(alpha=_alpha(15), adjust=False).mean()
        prior5 = _same_session_timestamp_values(ema5, frame.loc[index, "datetime"], 5)
        prior15 = _same_session_timestamp_values(ema15, frame.loc[index, "datetime"], 5)
        result.loc[index, "price_to_ema_5"] = prices.to_numpy() / ema5.to_numpy() - 1.0
        result.loc[index, "price_to_ema_15"] = prices.to_numpy() / ema15.to_numpy() - 1.0
        result.loc[index, "ema_5_slope_5m"] = ema5.to_numpy() - prior5
        result.loc[index, "ema_15_slope_5m"] = ema15.to_numpy() - prior15
        result.loc[index, "ema_5_minus_ema_15"] = ema5.to_numpy() - ema15.to_numpy()
    result.loc[warmup.to_numpy(), :] = np.nan
    result.attrs[EMA_WARMUP_COLUMN] = warmup.reset_index(drop=True)
    result.attrs["ema_half_lives"] = {"price_to_ema_5": 5, "price_to_ema_15": 15}
    return result


def ema_warmup_flag(df: pd.DataFrame) -> pd.Series:
    """Return true for the first 45 wall-clock minutes of every session."""
    frame, _, _ = _normalise_input(df)
    timestamps = frame["datetime"]
    starts = timestamps.groupby(frame["trade_date"], sort=False).transform("min")
    elapsed = (timestamps - starts).dt.total_seconds() / 60.0
    return elapsed.lt(45.0).rename(EMA_WARMUP_COLUMN)


def build_ema_features_with_warmup(df: pd.DataFrame) -> pd.DataFrame:
    """Return the five EMA features plus the non-model warm-up flag."""
    features = build_ema_features(df)
    features[EMA_WARMUP_COLUMN] = features.attrs[EMA_WARMUP_COLUMN].to_numpy()
    return features


def build_prediction_features_v1_1(
    df: pd.DataFrame,
    candidate: str = "S3",
) -> tuple[pd.DataFrame, list[str]]:
    """Build one exact S-ladder feature matrix in frozen manifest order."""
    name = _normalise_candidate(candidate)
    base, _ = build_prediction_features_v1(df, "V1-A")
    if name in {"S1", "S3"}:
        ema = build_ema_features(df).reset_index(drop=True)
        result = pd.concat([base.reset_index(drop=True), ema], axis=1)
    else:
        result = base.reset_index(drop=True)
    result = result.reindex(columns=FEATURE_MANIFESTS[name]).copy()
    assert list(result.columns) == FEATURE_MANIFESTS[name]
    assert_no_forbidden_columns(result)
    return result, list(FEATURE_MANIFESTS[name])


def build_candidate_features(df: pd.DataFrame, candidate: str) -> tuple[pd.DataFrame, list[str]]:
    """Explicit alias for building one registered S candidate."""
    return build_prediction_features_v1_1(df, candidate)


def _normalise_input(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    if not isinstance(df, pd.DataFrame) or df.empty:
        raise ValueError("df must be a non-empty DataFrame")
    if "datetime" not in df or "f_close" not in df:
        raise ValueError("EMA features require datetime and f_close")
    frame = df.copy()
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="raise")
    if frame["datetime"].duplicated().any():
        raise ValueError("duplicate timestamps are not allowed")
    frame = frame.sort_values("datetime", kind="stable").reset_index(drop=True)
    if "trade_date" not in frame:
        frame["trade_date"] = frame["datetime"].dt.normalize()
    else:
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise").dt.normalize()
    close = pd.to_numeric(frame["f_close"], errors="coerce")
    return frame, frame["trade_date"], close


def _same_session_timestamp_values(values: pd.Series, timestamps: pd.Series, minutes: int) -> np.ndarray:
    lookup = pd.Series(values.to_numpy(), index=timestamps).to_dict()
    return np.asarray([lookup.get(timestamp - pd.Timedelta(minutes=minutes), np.nan) for timestamp in timestamps], dtype=float)


def _alpha(half_life: float) -> float:
    return 1.0 - 2.0 ** (-1.0 / float(half_life))


def _normalise_candidate(candidate: str) -> str:
    name = str(candidate).strip().upper().replace("_", "-")
    aliases = {"0": "S0", "1": "S1", "2": "S2", "3": "S3"}
    name = aliases.get(name.removeprefix("S"), name)
    if name not in FEATURE_MANIFESTS:
        raise ValueError("candidate must be S0, S1, S2, or S3")
    return name
