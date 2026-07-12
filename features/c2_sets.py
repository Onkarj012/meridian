"""Outcome-blind Campaign 2 feature matrices.

This module deliberately consumes only futures minute bars, the trading
calendar/expiry calendar, and daily India VIX.  In particular, it neither
opens nor accepts labels or returns after a decision bar.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from collections import deque
from typing import Final, Literal

import numpy as np
import pandas as pd

from features.repaired import compute_repaired_features, repaired_features_multi


MINUTES_PER_SESSION: Final = 375
BASELINE_SESSIONS: Final = 60
MINIMUM_PRIOR_SESSIONS: Final = 20
MUHURAT_DATES: Final = frozenset(pd.Timestamp(value) for value in (
    "2020-11-14", "2021-11-04", "2022-10-24", "2023-11-12",
))
DROPPED_DATES: Final = frozenset(pd.Timestamp(value) for value in (
    "2021-02-24", "2025-09-26", "2026-06-03",
))
KEY_COLUMNS: Final = ["session_date", "datetime"]
ELIGIBILITY_COLUMNS: Final = [
    "is_session_eligible",
    "is_time_eligible",
    "has_contiguous_horizon_60m",
    "is_decision_eligible",
]

# This is the canonical, hashed registration.  Formula choices not specified
# by the roadmap are deliberately written here rather than hidden in code.
FEATURESET_CONFIG: Final[dict[str, object]] = {
    "shared": {
        "minute_session": "09:15 through 15:29 inclusive; trim later bars",
        "excluded_sessions": sorted(str(value.date()) for value in MUHURAT_DATES | DROPPED_DATES),
        "standardizers": {
            "sessions": BASELINE_SESSIONS,
            "minimum_valid_prior_sessions": MINIMUM_PRIOR_SESSIONS,
            "scope": "preceding completed retained regular sessions only; never current/future",
            "matched_minute": "minute-of-day for intraday fields",
            "z_score": "(current - prior_mean) / prior_population_std (ddof=0); zero std -> NaN",
        },
        "expiry": {
            "pre_2025_09_26_rule": "last Thursday of expiry month, holiday adjusted backward to observed archive trading day",
            "from_2025_09_26_rule": "last Tuesday of expiry month, holiday adjusted backward to observed archive trading day",
            "days_to_expiry": "count of observed trading dates D <= date <= front_expiry; expiry session is 1",
            "expiry_week": "1 when days_to_expiry <= 4, otherwise 0",
            "source_precedence": "contract_calendar front_expiry where present; validated monthly derivation fills its missing span",
        },
        "vix": {
            "availability": "session D uses its preceding observed trading session's VIX close only",
            "z252": "(VIX[T-1] - mean(last 252 VIX closes through T-1)) / population_std; <252 or zero std -> NaN",
            "return": "log(VIX[T-1] / VIX[T-2]); unavailable/nonpositive -> NaN",
        },
        "eligibility": {
            "time": "09:45..14:29 inclusive, excluding 11:00..11:59",
            "contiguity": "next 60 same-session minute bars must exist and be one-minute contiguous",
        },
    },
    "c2w": {
        "columns": [
            "ret_1m", "ret_5m", "ret_15m", "ret_30m", "ret_60m",
            "log_ret_1m", "log_ret_5m", "log_ret_15m", "log_ret_30m",
            "natr_5m", "natr_15m", "natr_30m",
            "realized_vol_5m", "realized_vol_15m", "realized_vol_30m",
            "vwap_dev", "vwap_slope_5m", "or_dist_high", "or_dist_low",
            "or_breakout_up", "or_breakout_dn", "minute_of_day", "hour_of_day",
            "session_progress", "day_of_week", "true_gap_pct", "consec_bars",
            "ema_slope", "volume_surprise_60d", "days_to_expiry", "expiry_week",
            "vix_t1_z252", "vix_t1_return",
        ],
        "consec_bars": "repaired zero-return-reset variant",
    },
    "c2p": {
        "columns": [
            "vol_normalized_ret_5m", "vol_normalized_ret_15m", "vol_normalized_ret_30m",
            "realized_vol_5m", "realized_vol_15m", "realized_vol_30m",
            "vwap_dev_z60", "vwap_slope_5m", "opening_range_location",
            "opening_range_width_z60", "trend_efficiency_15m", "trend_efficiency_30m",
            "pullback_depth_15m", "range_expansion_15m", "range_expansion_30m",
            "true_gap_pct", "gap_fill_fraction", "session_time_sin", "session_time_cos",
            "days_to_expiry", "expiry_week", "vix_t1_z252", "vix_t1_return",
        ],
        "vol_normalized_return": "ret_w / (realized_vol_30m at t * sqrt(w / 375)); nonpositive or missing denominator -> NaN",
        "opening_range_location": "(close - OR_low) / (OR_high - OR_low), clipped to [0, 1]; OR is 09:15..09:29 and values before 09:30 are NaN",
        "opening_range_width": "(OR_high - OR_low) / first_open; z-scored against preceding 60 completed sessions; <20 or zero std -> NaN",
        "trend_efficiency": "abs(close_t - close_t-w) / sum(abs(one-minute close changes), w changes); flat/missing window -> NaN",
        "pullback_depth": "(rolling_15_high - close) / (rolling_15_high - rolling_15_low); flat/missing window -> NaN",
        "range_expansion": "current rolling w-minute high-low range / median(the same w-minute range at same minute over preceding 60 sessions); <20 or nonpositive median -> NaN",
        "gap_fill_fraction": "clip((first_open - close_t) / (first_open - prior_session_close), 0, 1); zero/missing prior gap -> NaN",
        "session_time": "sin/cos(2*pi*session_progress)",
    },
}


def _config_hash(key: Literal["c2w", "c2p"]) -> str:
    payload = {"shared": FEATURESET_CONFIG["shared"], key: FEATURESET_CONFIG[key]}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def c2w_config_hash() -> str:
    """Return the canonical SHA-256 registration hash for C2-W."""
    return _config_hash("c2w")


def c2p_config_hash() -> str:
    """Return the canonical SHA-256 registration hash for C2-P."""
    return _config_hash("c2p")


def _normalise_raw(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    def parse_timestamps(values: object) -> pd.Series:
        parsed = pd.to_datetime(values)
        try:
            aware = parsed.dt.tz is not None  # type: ignore[union-attr]
        except AttributeError:
            aware = any(
                pd.Timestamp(value).tzinfo is not None and pd.Timestamp(value).utcoffset() is not None
                for value in parsed
            )
        if aware:
            raise ValueError("timestamps must be naive IST; convert tz-aware datetimes to naive IST before calling C2 builders")
        return parsed
    if "datetime" not in df:
        if {"date", "time"}.issubset(df.columns):
            df["datetime"] = parse_timestamps(df["date"].astype(str) + " " + df["time"].astype(str))
        elif "date" in df:
            df["datetime"] = parse_timestamps(df["date"])
        else:
            raise ValueError("raw data requires datetime or date/time columns")
    else:
        df["datetime"] = parse_timestamps(df["datetime"])
    df = df.rename(columns={"open": "f_open", "high": "f_high", "low": "f_low", "close": "f_close", "volume": "f_vol", "oi": "f_oi"})
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
    minute = df["datetime"].dt.hour * 60 + df["datetime"].dt.minute
    regular = (minute >= 9 * 60 + 15) & (minute <= 15 * 60 + 29)
    excluded = df["trade_date"].isin(MUHURAT_DATES | DROPPED_DATES)
    return df.loc[regular & ~excluded].sort_values("datetime", kind="stable").reset_index(drop=True)


def derive_monthly_expiries(trading_dates: pd.Series | pd.Index | list[object]) -> pd.DataFrame:
    """Derive monthly NIFTY expiries from observed trading dates, outcome-blind.

    The NSE monthly-expiry weekday changed from Thursday to Tuesday after the
    September 2025 Thursday expiry.  A holiday expiry is moved backward to the
    nearest observed trading day, as required by the campaign registration.
    """
    dates = pd.DatetimeIndex(pd.to_datetime(trading_dates)).normalize().unique().sort_values()
    if dates.empty:
        return pd.DataFrame(columns=["month", "derived_expiry"])
    months = pd.period_range(dates.min().to_period("M"), dates.max().to_period("M"), freq="M")
    rows: list[dict[str, object]] = []
    for month in months:
        # September 2025 is the transition month: the Thursday 25th expiry
        # remains valid and the new Tuesday monthly expiry is September 30th.
        if month < pd.Period("2025-09", freq="M"):
            weekdays = (3,)
        elif month == pd.Period("2025-09", freq="M"):
            weekdays = (3, 1)
        else:
            weekdays = (1,)
        last_day = month.end_time.normalize()
        for weekday in weekdays:
            target = last_day - pd.Timedelta(days=(last_day.weekday() - weekday) % 7)
            candidates = dates[(dates <= target) & (dates >= month.start_time.normalize())]
            if len(candidates):
                rows.append({"month": str(month), "derived_expiry": candidates[-1]})
    return pd.DataFrame(rows)


def validate_expiry_derivation(
    trading_dates: pd.Series | pd.Index | list[object], known_expiries: pd.DataFrame,
) -> dict[str, object]:
    """Validate all overlapping known expiry months and return a mechanical report."""
    derived = derive_monthly_expiries(trading_dates)
    known = known_expiries.copy()
    if "expiry" not in known:
        raise ValueError("known_expiries requires an expiry column")
    known["expiry"] = pd.to_datetime(known["expiry"]).dt.normalize()
    known["month"] = known["expiry"].dt.to_period("M").astype(str)
    derived_by_month = derived.groupby("month")["derived_expiry"].agg(set)
    compared = known[known["month"].isin(derived_by_month.index)].copy()
    compared["derived_expiries"] = compared["month"].map(derived_by_month)
    matches = (compared.apply(lambda row: row["expiry"] in row["derived_expiries"], axis=1)
               if not compared.empty else pd.Series(dtype=bool))
    return {
        "overlap_count": len(compared),
        "match_count": int(matches.sum()),
        "match_rate": float(matches.mean()) if len(compared) else np.nan,
        "mismatches": compared.loc[~matches, ["month", "expiry", "derived_expiries"]].to_dict("records"),
    }


def _front_expiry_by_date(
    sessions: pd.DatetimeIndex,
    trading_dates: pd.DatetimeIndex,
    known_expiries: pd.DataFrame | None,
    contract_calendar: pd.DataFrame | None,
) -> pd.Series:
    derived = derive_monthly_expiries(trading_dates)
    expiry_dates = pd.DatetimeIndex(derived["derived_expiry"]).sort_values()
    # Known records are authoritative where available; derivation fills only
    # their missing span, preserving the requested validation separation.
    if known_expiries is not None and not known_expiries.empty:
        known = pd.to_datetime(known_expiries["expiry"]).dt.normalize()
        expiry_dates = pd.DatetimeIndex(sorted(set(expiry_dates).union(set(known))))
    positions = expiry_dates.searchsorted(sessions, side="left")
    values = pd.Series(
        [expiry_dates[position] if position < len(expiry_dates) else pd.NaT for position in positions], index=sessions
    )
    if contract_calendar is not None and not contract_calendar.empty:
        calendar = contract_calendar.copy()
        required = {"trade_date", "front_expiry"}
        missing = required.difference(calendar.columns)
        if missing:
            raise ValueError(f"contract_calendar missing required columns: {', '.join(sorted(missing))}")
        calendar["trade_date"] = pd.to_datetime(calendar["trade_date"]).dt.normalize()
        calendar["front_expiry"] = pd.to_datetime(calendar["front_expiry"]).dt.normalize()
        known_front = calendar.drop_duplicates("trade_date", keep="last").set_index("trade_date")["front_expiry"]
        values = values.where(~values.index.isin(known_front.index), values.index.to_series().map(known_front))
    return values


def _days_to_expiry(sessions: pd.DatetimeIndex, expiry_by_date: pd.Series, trading_dates: pd.DatetimeIndex) -> pd.Series:
    positions = {value: index for index, value in enumerate(trading_dates)}
    values: list[float] = []
    for session in sessions:
        expiry = expiry_by_date.loc[session]
        if pd.isna(expiry) or session not in positions or expiry not in positions:
            values.append(np.nan)
        else:
            values.append(float(positions[expiry] - positions[session] + 1))
    return pd.Series(values, index=sessions)


def _vix_features(sessions: pd.DatetimeIndex, trading_dates: pd.DatetimeIndex, vix: pd.DataFrame | None) -> pd.DataFrame:
    result = pd.DataFrame(index=sessions, data={"vix_t1_z252": np.nan, "vix_t1_return": np.nan})
    if vix is None or vix.empty:
        return result
    source = vix.copy()
    date_column = "date" if "date" in source else "datetime"
    source[date_column] = pd.to_datetime(source[date_column]).dt.normalize()
    close_column = "close" if "close" in source else "vix_close"
    closes = source.drop_duplicates(date_column, keep="last").set_index(date_column)[close_column].astype(float).sort_index()
    trading_positions = {value: position for position, value in enumerate(trading_dates)}
    for session in sessions:
        position = trading_positions.get(session)
        if position is None or position == 0:
            continue
        t1_date = trading_dates[position - 1]
        if t1_date not in closes.index:
            continue
        history = closes.loc[:t1_date]
        t1 = float(history.iloc[-1])
        if len(history) >= 252:
            trailing = history.iloc[-252:]
            std = float(trailing.std(ddof=0))
            if std > 0:
                result.loc[session, "vix_t1_z252"] = (t1 - float(trailing.mean())) / std
        if len(history) >= 2 and t1 > 0 and history.iloc[-2] > 0:
            result.loc[session, "vix_t1_return"] = np.log(t1 / float(history.iloc[-2]))
    return result


def _same_minute_baseline(
    df: pd.DataFrame, value_column: str, kind: Literal["zscore", "median"], output_column: str,
) -> pd.DataFrame:
    """Return causal prior-session same-minute values keyed to input rows."""
    sessions = pd.DatetimeIndex(df["trade_date"].drop_duplicates().sort_values())
    session_position = {session: position for position, session in enumerate(sessions)}
    values = df.groupby(["trade_date", "minute_of_day"], sort=False)[value_column].median()
    rows: list[dict[str, object]] = []
    for minute, per_session in values.groupby(level="minute_of_day", sort=False):
        per_session = per_session.droplevel("minute_of_day")
        for session, current in per_session.items():
            position = session_position[session]
            prior_dates = sessions[max(0, position - BASELINE_SESSIONS):position]
            prior = per_session.reindex(prior_dates).dropna()
            value = np.nan
            if len(prior) >= MINIMUM_PRIOR_SESSIONS:
                if kind == "median":
                    median = float(prior.median())
                    if median > 0:
                        value = float(current) / median
                else:
                    std = float(prior.std(ddof=0))
                    if std > 0:
                        value = (float(current) - float(prior.mean())) / std
            rows.append({"trade_date": session, "minute_of_day": int(minute), output_column: value})
    lookup = pd.DataFrame(rows)
    return df[["trade_date", "minute_of_day"]].merge(lookup, on=["trade_date", "minute_of_day"], how="left")[output_column]


def _session_zscore(df: pd.DataFrame, values: pd.Series, output_column: str) -> pd.Series:
    sessions = pd.DatetimeIndex(df["trade_date"].drop_duplicates().sort_values())
    per_session = pd.Series(values.to_numpy(), index=df["trade_date"]).groupby(level=0).first().reindex(sessions)
    result: dict[pd.Timestamp, float] = {}
    for position, session in enumerate(sessions):
        prior = per_session.iloc[max(0, position - BASELINE_SESSIONS):position].dropna()
        std = float(prior.std(ddof=0)) if len(prior) >= MINIMUM_PRIOR_SESSIONS else 0.0
        result[session] = ((float(per_session.loc[session]) - float(prior.mean())) / std
                           if std > 0 and pd.notna(per_session.loc[session]) else np.nan)
    return df["trade_date"].map(result).rename(output_column)


def _add_path_features(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    grouped_close = result["f_close"].groupby(result["trade_date"], sort=False)
    grouped_high = result["f_high"].groupby(result["trade_date"], sort=False)
    grouped_low = result["f_low"].groupby(result["trade_date"], sort=False)

    def grouped_rolling(values: pd.Series, window: int, method: str) -> pd.Series:
        rolled = values.groupby(result["trade_date"], sort=False).rolling(window, min_periods=window)
        output = getattr(rolled, method)().reset_index(level=0, drop=True)
        return output.sort_index()

    minute = result["minute_of_day"]
    after_or = minute >= 9 * 60 + 30
    denominator = result["realized_vol_30m"]
    for window in (5, 15, 30):
        scaled = denominator * np.sqrt(window / MINUTES_PER_SESSION)
        result[f"vol_normalized_ret_{window}m"] = result[f"ret_{window}m"] / scaled.where(scaled > 0)

    result["vwap_dev_z60"] = _same_minute_baseline(result, "vwap_dev", "zscore", "vwap_dev_z60")
    or_width = (result["or_high"] - result["or_low"]) / result["f_open"].groupby(result["trade_date"]).transform("first").replace(0, np.nan)
    result["opening_range_width_z60"] = _session_zscore(result, or_width, "opening_range_width_z60")
    or_range = result["or_high"] - result["or_low"]
    result["opening_range_location"] = ((result["f_close"] - result["or_low"]) / or_range.where(or_range > 0)).clip(0, 1).where(after_or)
    for window in (15, 30):
        net = grouped_close.diff(window).abs()
        travel = grouped_rolling(grouped_close.diff().abs(), window, "sum")
        result[f"trend_efficiency_{window}m"] = (net / travel.where(travel > 0)).clip(0, 1)
    high = grouped_rolling(result["f_high"], 15, "max")
    low = grouped_rolling(result["f_low"], 15, "min")
    result["pullback_depth_15m"] = ((high - result["f_close"]) / (high - low).where((high - low) > 0)).clip(0, 1)
    for window in (15, 30):
        range_column = f"rolling_range_{window}m"
        result[range_column] = (
            grouped_rolling(result["f_high"], window, "max")
            - grouped_rolling(result["f_low"], window, "min")
        )
        result[f"range_expansion_{window}m"] = _same_minute_baseline(
            result, range_column, "median", f"range_expansion_{window}m"
        )
    prior_close = result["prior_session_close"]
    first_open = result["f_open"].groupby(result["trade_date"]).transform("first")
    gap = first_open - prior_close
    result["gap_fill_fraction"] = ((first_open - result["f_close"]) / gap.where(gap != 0)).clip(0, 1)
    angle = 2 * np.pi * result["session_progress"]
    result["session_time_sin"] = np.sin(angle)
    result["session_time_cos"] = np.cos(angle)
    return result


def _add_eligibility(df: pd.DataFrame) -> pd.DataFrame:
    result = df.copy()
    result["is_session_eligible"] = np.int8(1)
    minute = result["minute_of_day"]
    in_time = (minute >= 9 * 60 + 45) & (minute <= 14 * 60 + 29) & ~((minute >= 11 * 60) & (minute < 12 * 60))
    contiguous = np.zeros(len(result), dtype=np.int8)
    for _, positions in result.groupby("trade_date", sort=False).indices.items():
        indexes = np.asarray(positions)
        datetimes = result.loc[indexes, "datetime"].to_numpy(dtype="datetime64[m]")
        for offset in range(len(indexes)):
            if offset + 60 < len(indexes):
                differences = np.diff(datetimes[offset:offset + 61]).astype("timedelta64[m]").astype(int)
                contiguous[indexes[offset]] = int(np.all(differences == 1))
    result["is_time_eligible"] = in_time.astype(np.int8)
    result["has_contiguous_horizon_60m"] = contiguous
    result["is_decision_eligible"] = (result["is_time_eligible"] & result["has_contiguous_horizon_60m"]).astype(np.int8)
    return result


def _build_all(
    raw: pd.DataFrame,
    *,
    known_expiries: pd.DataFrame | None = None,
    contract_calendar: pd.DataFrame | None = None,
    vix: pd.DataFrame | None = None,
    trading_dates: pd.Series | pd.Index | list[object] | None = None,
) -> pd.DataFrame:
    if trading_dates is not None:
        original_dates = pd.DatetimeIndex(pd.to_datetime(trading_dates)).normalize().unique().sort_values()
    else:
        source_dates = raw["date"] if "date" in raw else raw["datetime"]
        original_dates = pd.DatetimeIndex(pd.to_datetime(source_dates)).normalize().unique().sort_values()
    clean = _normalise_raw(raw)
    if clean.empty:
        return pd.DataFrame(columns=KEY_COLUMNS + ELIGIBILITY_COLUMNS)
    features = repaired_features_multi(clean)
    features["trade_date"] = pd.to_datetime(features["trade_date"]).dt.normalize()
    sessions = pd.DatetimeIndex(features["trade_date"].drop_duplicates().sort_values())
    usable_dates = original_dates.union(sessions).sort_values()
    expiry = _front_expiry_by_date(sessions, usable_dates, known_expiries, contract_calendar)
    dte = _days_to_expiry(sessions, expiry, usable_dates)
    features["days_to_expiry"] = features["trade_date"].map(dte)
    features["expiry_week"] = (features["days_to_expiry"] <= 4).where(features["days_to_expiry"].notna()).astype("Float64")
    vix_features = _vix_features(sessions, usable_dates, vix)
    for column in vix_features:
        features[column] = features["trade_date"].map(vix_features[column])
    prior_close = features.groupby("trade_date", sort=True)["f_close"].last().shift(1)
    features["prior_session_close"] = features["trade_date"].map(prior_close)
    features["consec_bars"] = features["repaired_consec_bars"]
    # OR-dependent values cannot be known before the 09:15..09:29 range has
    # completed, even though only later bars are registered decision times.
    before_or_complete = features["minute_of_day"] < 9 * 60 + 30
    features.loc[before_or_complete, ["or_dist_high", "or_dist_low", "or_breakout_up", "or_breakout_dn"]] = np.nan
    features = _add_path_features(features)
    features = _add_eligibility(features)
    features = features.rename(columns={"trade_date": "session_date"})
    return features


def _select(matrix: pd.DataFrame, kind: Literal["c2w", "c2p"]) -> pd.DataFrame:
    columns = KEY_COLUMNS + list(FEATURESET_CONFIG[kind]["columns"]) + ELIGIBILITY_COLUMNS
    selected = matrix.reindex(columns=columns).sort_values("datetime", kind="stable").reset_index(drop=True)
    selected["session_date"] = pd.to_datetime(selected["session_date"]).astype("datetime64[ns]")
    selected["expiry_week"] = selected["expiry_week"].astype(float)
    return selected


def build_c2w_matrix(raw: pd.DataFrame, **kwargs: object) -> pd.DataFrame:
    """Build the 33-input C2-W matrix from outcome-blind source inputs."""
    return _select(_build_all(raw, **kwargs), "c2w")


def build_c2p_matrix(raw: pd.DataFrame, **kwargs: object) -> pd.DataFrame:
    """Build the 23-input C2-P matrix from outcome-blind source inputs."""
    return _select(_build_all(raw, **kwargs), "c2p")


def build_c2_matrices(raw: pd.DataFrame, **kwargs: object) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build both sets once; used by the CLI to avoid duplicate feature work."""
    matrix = _build_all(raw, **kwargs)
    return _select(matrix, "c2w"), _select(matrix, "c2p")


def iter_c2_matrices(
    raw: pd.DataFrame | object, *, known_expiries: pd.DataFrame | None = None,
    contract_calendar: pd.DataFrame | None = None, vix: pd.DataFrame | None = None,
    trading_dates: pd.Series | pd.Index | list[object] | None = None,
):
    """Yield one-session C2 matrices using bounded causal state.

    This is the archive driver's memory-bounded equivalent of
    :func:`build_c2_matrices`.  Its per-minute state is precisely the last 60
    completed session maps, so no future session can enter a baseline.
    """
    if trading_dates is not None:
        all_dates = pd.DatetimeIndex(pd.to_datetime(trading_dates)).normalize().unique().sort_values()
    elif isinstance(raw, pd.DataFrame):
        source = raw["date"] if "date" in raw else raw["datetime"]
        all_dates = pd.DatetimeIndex(pd.to_datetime(source)).normalize().unique().sort_values()
    else:
        raise ValueError("trading_dates is required when raw is a day-frame iterable")
    if isinstance(raw, pd.DataFrame):
        source_dates = raw["date"] if "date" in raw else raw["datetime"]
        arrival_dates = pd.DatetimeIndex(pd.to_datetime(source_dates)).normalize().drop_duplicates()
        if any(
            right <= left
            for left, right in zip(arrival_dates[:-1], arrival_dates[1:], strict=True)
        ):
            raise ValueError("iter_c2_matrices sessions must arrive in strictly increasing trade_date order")
        clean = _normalise_raw(raw)
        if clean.empty:
            return
        sessions = pd.DatetimeIndex(clean["trade_date"].drop_duplicates().sort_values())
        day_groups = clean.groupby("trade_date", sort=True)
    else:
        sessions = pd.DatetimeIndex(date for date in all_dates if date not in MUHURAT_DATES | DROPPED_DATES)
        def normalised_days():
            for source_day in raw:
                day = _normalise_raw(source_day)
                if not day.empty:
                    yield day["trade_date"].iat[0], day
        day_groups = normalised_days()
    all_dates = all_dates.union(sessions).sort_values()
    expiry = _front_expiry_by_date(sessions, all_dates, known_expiries, contract_calendar)
    dte = _days_to_expiry(sessions, expiry, all_dates)
    vix_fields = _vix_features(sessions, all_dates, vix)
    volume_history: deque[dict[int, float]] = deque(maxlen=BASELINE_SESSIONS)
    vwap_history: deque[dict[int, float]] = deque(maxlen=BASELINE_SESSIONS)
    range15_history: deque[dict[int, float]] = deque(maxlen=BASELINE_SESSIONS)
    range30_history: deque[dict[int, float]] = deque(maxlen=BASELINE_SESSIONS)
    width_history: deque[float] = deque(maxlen=BASELINE_SESSIONS)
    prior_close: float | None = None

    def prior_map_value(history: deque[dict[int, float]], minute: int, current: float, mode: str) -> float:
        prior = np.array([mapping.get(minute, np.nan) for mapping in history], dtype=float)
        prior = prior[np.isfinite(prior)]
        if len(prior) < MINIMUM_PRIOR_SESSIONS:
            return np.nan
        if mode == "median":
            median = float(np.median(prior))
            return current / median if median > 0 and np.isfinite(current) else np.nan
        std = float(np.std(prior))
        return (current - float(np.mean(prior))) / std if std > 0 and np.isfinite(current) else np.nan

    previous_session: pd.Timestamp | None = None
    for session, day in day_groups:
        if previous_session is not None and session <= previous_session:
            raise ValueError("iter_c2_matrices sessions must arrive in strictly increasing trade_date order")
        previous_session = session
        minute = (day["datetime"].dt.hour * 60 + day["datetime"].dt.minute).astype(int)
        volume_by_minute = day.assign(minute_of_day=minute).groupby("minute_of_day")["f_vol"].median().to_dict()
        baseline_rows = [{
            "trade_date": session, "minute_of_day": key,
            "volume_median_baseline_60d": (lambda prior: float(np.median(prior)) if len(prior) >= MINIMUM_PRIOR_SESSIONS else np.nan)(
                np.array([mapping.get(key, np.nan) for mapping in volume_history], dtype=float)[np.isfinite(np.array([mapping.get(key, np.nan) for mapping in volume_history], dtype=float))]
            ),
        } for key in volume_by_minute]
        frame = compute_repaired_features(day, session.date(), prior_close, pd.DataFrame(baseline_rows))
        frame["trade_date"] = session
        frame["consec_bars"] = frame["repaired_consec_bars"]
        frame["days_to_expiry"] = dte.loc[session]
        frame["expiry_week"] = float(frame["days_to_expiry"].iat[0] <= 4) if pd.notna(frame["days_to_expiry"].iat[0]) else np.nan
        frame["vix_t1_z252"] = vix_fields.loc[session, "vix_t1_z252"]
        frame["vix_t1_return"] = vix_fields.loc[session, "vix_t1_return"]
        after_or = frame["minute_of_day"] >= 570
        frame.loc[~after_or, ["or_dist_high", "or_dist_low", "or_breakout_up", "or_breakout_dn"]] = np.nan
        for window in (5, 15, 30):
            scale = frame["realized_vol_30m"] * np.sqrt(window / MINUTES_PER_SESSION)
            frame[f"vol_normalized_ret_{window}m"] = frame[f"ret_{window}m"] / scale.where(scale > 0)
        frame["vwap_dev_z60"] = [
            prior_map_value(vwap_history, int(m), float(x), "z")
            for m, x in zip(frame["minute_of_day"], frame["vwap_dev"], strict=True)
        ]
        first_open = float(frame["f_open"].iat[0])
        width = (float(frame["or_high"].iat[0]) - float(frame["or_low"].iat[0])) / first_open if first_open else np.nan
        valid_width = np.array([x for x in width_history if np.isfinite(x)], dtype=float)
        frame["opening_range_width_z60"] = ((width - valid_width.mean()) / valid_width.std()
                                              if len(valid_width) >= MINIMUM_PRIOR_SESSIONS and valid_width.std() > 0 else np.nan)
        or_range = (frame["or_high"] - frame["or_low"]).where(lambda x: x > 0)
        frame["opening_range_location"] = ((frame["f_close"] - frame["or_low"]) / or_range).clip(0, 1).where(after_or)
        for window, history in ((15, range15_history), (30, range30_history)):
            travel = frame["f_close"].diff().abs().rolling(window, min_periods=window).sum()
            frame[f"trend_efficiency_{window}m"] = (frame["f_close"].diff(window).abs() / travel.where(travel > 0)).clip(0, 1)
            rr = frame["f_high"].rolling(window, min_periods=window).max() - frame["f_low"].rolling(window, min_periods=window).min()
            frame[f"range_expansion_{window}m"] = [
                prior_map_value(history, int(m), float(x), "median")
                for m, x in zip(frame["minute_of_day"], rr, strict=True)
            ]
            frame[f"_rr{window}"] = rr
        high = frame["f_high"].rolling(15, min_periods=15).max()
        low = frame["f_low"].rolling(15, min_periods=15).min()
        frame["pullback_depth_15m"] = ((high - frame["f_close"]) / (high - low).where((high - low) > 0)).clip(0, 1)
        gap = first_open - prior_close if prior_close is not None else np.nan
        frame["gap_fill_fraction"] = (((first_open - frame["f_close"]) / gap).clip(0, 1)
                                      if np.isfinite(gap) and gap != 0 else pd.Series(np.nan, index=frame.index))
        angle = 2 * np.pi * frame["session_progress"]; frame["session_time_sin"] = np.sin(angle); frame["session_time_cos"] = np.cos(angle)
        frame = _add_eligibility(frame)
        frame = frame.rename(columns={"trade_date": "session_date"})
        yield _select(frame, "c2w"), _select(frame, "c2p")
        vwap_history.append(frame.set_index("minute_of_day")["vwap_dev"].to_dict())
        range15_history.append(frame.set_index("minute_of_day")["_rr15"].to_dict())
        range30_history.append(frame.set_index("minute_of_day")["_rr30"].to_dict())
        volume_history.append(volume_by_minute)
        width_history.append(width)
        prior_close = float(day["f_close"].iloc[-1])
