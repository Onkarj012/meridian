"""Causal external-data adapters for the V1 intraday prediction features."""
from __future__ import annotations

from collections.abc import Iterable
from hashlib import sha256
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


DEFAULT_NIFTY_SPOT_PATH = Path(
    "/Users/onkarj012/Projects/market/intranet_optinet/data/nifty_intraday/NIFTY 50_minute.csv"
)
DEFAULT_BANKNIFTY_PATH = Path(
    "/Users/onkarj012/Projects/market/intranet_optinet/data/banknifty_intraday/bank-nifty-1m-data.csv"
)
DEFAULT_VIX_PATH = Path(
    "/Users/onkarj012/Projects/market/intranet_optinet/data/nifty_intraday/INDIA VIX_day.csv"
)

MINUTE_SESSION_START = 9 * 60 + 15
MINUTE_SESSION_END = 15 * 60 + 29
ANNUALIZED_MINUTES = np.sqrt(252 * 375)
VIX_MAX_STALENESS_DAYS = 5

SPOT_FEATURE_COLUMNS = (
    "spot_log_ret_1m",
    "spot_log_ret_5m",
    "spot_log_ret_15m",
    "spot_log_ret_30m",
    "spot_log_ret_60m",
    "spot_rv_15m",
    "spot_rv_60m",
    "spot_missing",
)
BASIS_FEATURE_COLUMNS = (
    "basis_bps_l1",
    "basis_delta_5m",
    "basis_delta_15m",
    "basis_delta_30m",
    "basis_z_60m",
    "basis_missing",
)
BANK_FEATURE_COLUMNS = (
    "bank_log_ret_5m",
    "bank_log_ret_15m",
    "bank_log_ret_30m",
    "bank_log_ret_60m",
    "bank_rel_ret_5m",
    "bank_rel_ret_15m",
    "bank_rel_ret_30m",
    "bank_rel_ret_60m",
    "bank_spot_rv_ratio_30m",
    "bank_missing",
)
VIX_FEATURE_COLUMNS = (
    "vix_close_l1d",
    "vix_log_ret_1d",
    "vix_log_ret_5d",
    "vix_z20",
    "vix_range_l1d",
    "vix_stale_calendar_days",
    "vix_missing",
)


def load_nifty_spot(
    path: str | Path = DEFAULT_NIFTY_SPOT_PATH,
    cutoff: pd.Timestamp | str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read and audit the ISO-timestamp NIFTY spot minute source."""
    raw = pd.read_csv(path)
    required = {"date", "open", "high", "low", "close"}
    _require_columns(raw, required, "NIFTY spot")
    frame = raw.rename(columns={"date": "timestamp"}).copy()
    frame["timestamp"] = _parse_timestamps(frame["timestamp"], dayfirst=False)
    return _prepare_minute_source(frame, path, cutoff, ("open", "high", "low", "close", "volume"))


def load_banknifty(
    path: str | Path = DEFAULT_BANKNIFTY_PATH,
    cutoff: pd.Timestamp | str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read and audit the DMY BANKNIFTY minute source."""
    raw = pd.read_csv(path)
    required = {"Instrument", "Date", "Time", "Open", "High", "Low", "Close"}
    _require_columns(raw, required, "BANKNIFTY")
    frame = raw.rename(
        columns={"Date": "date", "Time": "time", "Open": "open", "High": "high", "Low": "low", "Close": "close"}
    ).copy()
    frame["timestamp"] = _parse_timestamps(frame["date"].astype(str) + " " + frame["time"].astype(str), dayfirst=True)
    return _prepare_minute_source(frame, path, cutoff, ("open", "high", "low", "close"))


def load_vix(
    path: str | Path = DEFAULT_VIX_PATH,
    cutoff: pd.Timestamp | str | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read and audit the mixed date/date-time daily VIX source."""
    raw = pd.read_csv(path)
    required = {"date", "open", "high", "low", "close"}
    _require_columns(raw, required, "VIX")
    frame = raw.copy()
    frame["timestamp"] = _parse_timestamps(frame["date"], dayfirst=False)
    frame, audit = _prepare_daily_source(frame, path, cutoff, ("open", "high", "low", "close", "volume"))
    frame["source_date"] = frame["timestamp"].dt.normalize()
    duplicate_dates = frame["source_date"].duplicated(keep=False)
    if duplicate_dates.any():
        duplicate_rows = frame.loc[duplicate_dates]
        collapsed = int(len(duplicate_rows) - duplicate_rows["source_date"].nunique())
        frame = frame.sort_values("timestamp", kind="stable").drop_duplicates("source_date", keep="last")
        audit["duplicates_collapsed"] += collapsed
    frame["timestamp"] = frame["source_date"]
    frame = frame.drop(columns="source_date").sort_values("timestamp", kind="stable").reset_index(drop=True)
    audit["accepted_rows"] = len(frame)
    audit["min_accepted_timestamp"] = str(frame["timestamp"].min()) if not frame.empty else None
    audit["max_accepted_timestamp"] = str(frame["timestamp"].max()) if not frame.empty else None
    return frame, audit


def load_intraday_external_features(
    decision_rows: pd.DataFrame,
    cutoff: pd.Timestamp | str,
    mode: str = "V1-C",
    *,
    nifty_path: str | Path = DEFAULT_NIFTY_SPOT_PATH,
    banknifty_path: str | Path = DEFAULT_BANKNIFTY_PATH,
    vix_path: str | Path = DEFAULT_VIX_PATH,
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    """Return causally aligned V1 external features and per-source audits.

    Minute values are joined only at the exact ``decision timestamp - 1``
    minute.  Daily VIX values are joined from the latest source date strictly
    before the decision trade date and are rejected once stale.
    """
    mode_name = _normalise_mode(mode)
    decisions, dates = _normalise_decisions(decision_rows)
    cutoff_ts = _parse_cutoff(cutoff)
    source_audits: dict[str, dict[str, Any]] = {}

    spot, spot_audit = load_nifty_spot(nifty_path, cutoff_ts)
    source_audits["nifty_spot"] = spot_audit
    bank: pd.DataFrame | None = None
    vix: pd.DataFrame | None = None
    if mode_name == "V1-C":
        bank, bank_audit = load_banknifty(banknifty_path, cutoff_ts)
        vix, vix_audit = load_vix(vix_path, cutoff_ts)
        source_audits["banknifty"] = bank_audit
        source_audits["vix"] = vix_audit

    result = pd.DataFrame(index=decisions.index)
    if mode_name in {"V1-B", "V1-C"}:
        result = _build_spot_features(result, decisions["datetime"], dates, spot, source_audits["nifty_spot"])
        result = _build_basis_features(result, decisions, dates, spot)
    if mode_name == "V1-C":
        assert bank is not None and vix is not None
        result = _build_bank_features(result, decisions["datetime"], dates, spot, bank, source_audits["banknifty"])
        result = _build_vix_features(result, dates, vix, source_audits["vix"])
    return result, source_audits


def _build_spot_features(
    result: pd.DataFrame,
    timestamps: pd.Series,
    dates: pd.Series,
    source: pd.DataFrame,
    audit: dict[str, Any],
) -> pd.DataFrame:
    close = _series_by_timestamp(source, "close")
    values = {column: np.full(len(timestamps), np.nan, dtype=float) for column in SPOT_FEATURE_COLUMNS[:-1]}
    missing = np.ones(len(timestamps), dtype="int8")
    matched = 0
    for position, (timestamp, trade_date) in enumerate(zip(timestamps, dates, strict=True)):
        end = timestamp - pd.Timedelta(minutes=1)
        exact = _exact_minute(close, end, trade_date)
        if exact is not None:
            matched += 1
            missing[position] = 0
        for lag in (1, 5, 15, 30, 60):
            value = _log_return(close, end, lag, trade_date)
            values[f"spot_log_ret_{lag}m"][position] = value
        for window in (15, 60):
            values[f"spot_rv_{window}m"][position] = _realized_vol(close, end, window, trade_date)
    for column, column_values in values.items():
        result[column] = column_values
    result["spot_missing"] = missing
    _set_join_audit(audit, matched, len(timestamps))
    return result


def _build_basis_features(
    result: pd.DataFrame,
    decisions: pd.DataFrame,
    dates: pd.Series,
    spot: pd.DataFrame,
) -> pd.DataFrame:
    spot_close = _series_by_timestamp(spot, "close")
    futures = _series_by_timestamp(decisions, "f_close") if "f_close" in decisions else pd.Series(dtype=float)
    basis = pd.Series(
        10_000 * (futures / spot_close - 1),
        dtype="float64",
    ).dropna()
    timestamps = decisions["datetime"]
    values = {column: np.full(len(timestamps), np.nan, dtype=float) for column in BASIS_FEATURE_COLUMNS[:-1]}
    missing = np.ones(len(timestamps), dtype="int8")
    for position, (timestamp, trade_date) in enumerate(zip(timestamps, dates, strict=True)):
        end = timestamp - pd.Timedelta(minutes=1)
        current = _exact_minute(basis, end, trade_date)
        if current is not None:
            missing[position] = 0
        values["basis_bps_l1"][position] = current if current is not None else np.nan
        for lag in (5, 15, 30):
            prior = _exact_minute(basis, end - pd.Timedelta(minutes=lag), trade_date)
            if current is not None and prior is not None:
                values[f"basis_delta_{lag}m"][position] = current - prior
        window = _window_values(basis, end, 60, trade_date)
        if current is not None and len(window) >= 30:
            std = float(window.std(ddof=1))
            if std > 0:
                values["basis_z_60m"][position] = (current - float(window.mean())) / std
    for column, column_values in values.items():
        result[column] = column_values
    result["basis_missing"] = missing
    return result


def _build_bank_features(
    result: pd.DataFrame,
    timestamps: pd.Series,
    dates: pd.Series,
    spot: pd.DataFrame,
    bank: pd.DataFrame,
    audit: dict[str, Any],
) -> pd.DataFrame:
    spot_close = _series_by_timestamp(spot, "close")
    bank_close = _series_by_timestamp(bank, "close")
    values = {column: np.full(len(timestamps), np.nan, dtype=float) for column in BANK_FEATURE_COLUMNS[:-1]}
    missing = np.ones(len(timestamps), dtype="int8")
    matched = 0
    for position, (timestamp, trade_date) in enumerate(zip(timestamps, dates, strict=True)):
        end = timestamp - pd.Timedelta(minutes=1)
        bank_exact = _exact_minute(bank_close, end, trade_date)
        if bank_exact is not None:
            matched += 1
            missing[position] = 0
        spot_returns = {}
        bank_returns = {}
        for lag in (5, 15, 30, 60):
            spot_returns[lag] = _log_return(spot_close, end, lag, trade_date)
            bank_returns[lag] = _log_return(bank_close, end, lag, trade_date)
            values[f"bank_log_ret_{lag}m"][position] = bank_returns[lag]
            if np.isfinite(bank_returns[lag]) and np.isfinite(spot_returns[lag]):
                values[f"bank_rel_ret_{lag}m"][position] = bank_returns[lag] - spot_returns[lag]
        bank_rv = _realized_vol(bank_close, end, 30, trade_date)
        spot_rv = _realized_vol(spot_close, end, 30, trade_date)
        if np.isfinite(bank_rv) and np.isfinite(spot_rv):
            values["bank_spot_rv_ratio_30m"][position] = np.log((bank_rv + 1e-12) / (spot_rv + 1e-12))
    for column, column_values in values.items():
        result[column] = column_values
    result["bank_missing"] = missing
    _set_join_audit(audit, matched, len(timestamps))
    return result


def _build_vix_features(
    result: pd.DataFrame,
    dates: pd.Series,
    vix: pd.DataFrame,
    audit: dict[str, Any],
) -> pd.DataFrame:
    values = {column: np.full(len(dates), np.nan, dtype=float) for column in VIX_FEATURE_COLUMNS[:-2]}
    stale = np.full(len(dates), np.nan, dtype=float)
    missing = np.ones(len(dates), dtype="int8")
    source_dates = vix["timestamp"].to_numpy(dtype="datetime64[ns]")
    close = vix["close"].to_numpy(dtype=float)
    high = vix["high"].to_numpy(dtype=float)
    low = vix["low"].to_numpy(dtype=float)
    matched = 0
    for position, trade_date in enumerate(dates):
        trade_day = pd.Timestamp(trade_date).normalize()
        index = int(np.searchsorted(source_dates, np.datetime64(trade_day), side="left") - 1)
        if index < 0:
            continue
        stale_days = (trade_day - pd.Timestamp(source_dates[index])).days
        stale[position] = stale_days
        if stale_days > VIX_MAX_STALENESS_DAYS:
            continue
        matched += 1
        missing[position] = 0
        values["vix_close_l1d"][position] = close[index]
        if index >= 1 and close[index] > 0 and close[index - 1] > 0:
            values["vix_log_ret_1d"][position] = np.log(close[index] / close[index - 1])
        if index >= 5 and close[index] > 0 and close[index - 5] > 0:
            values["vix_log_ret_5d"][position] = np.log(close[index] / close[index - 5])
        if index >= 20:
            history = close[index - 20:index]
            history = history[np.isfinite(history)]
            if len(history) == 20:
                history_std = float(np.std(history, ddof=1))
                if history_std > 0:
                    values["vix_z20"][position] = (close[index] - float(np.mean(history))) / history_std
        if close[index] > 0:
            values["vix_range_l1d"][position] = (high[index] - low[index]) / close[index]
    for column, column_values in values.items():
        result[column] = column_values
    result["vix_stale_calendar_days"] = stale
    result["vix_missing"] = missing
    _set_join_audit(audit, matched, len(dates))
    return result


def _prepare_minute_source(
    frame: pd.DataFrame,
    path: str | Path,
    cutoff: pd.Timestamp | None,
    payload: Iterable[str],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    return _prepare_source(frame, path, cutoff, payload, minute=True)


def _prepare_daily_source(
    frame: pd.DataFrame,
    path: str | Path,
    cutoff: pd.Timestamp | None,
    payload: Iterable[str],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    return _prepare_source(frame, path, cutoff, payload, minute=False)


def _prepare_source(
    frame: pd.DataFrame,
    path: str | Path,
    cutoff: pd.Timestamp | None,
    payload: Iterable[str],
    *,
    minute: bool,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    payload_columns = tuple(column for column in payload if column in frame.columns)
    raw_rows = len(frame)
    if cutoff is not None:
        frame = frame.loc[frame["timestamp"] < cutoff].copy()
    off_session_rows = 0
    if minute:
        minute_of_day = frame["timestamp"].dt.hour * 60 + frame["timestamp"].dt.minute
        accepted_mask = minute_of_day.between(MINUTE_SESSION_START, MINUTE_SESSION_END)
        off_session_rows = int((~accepted_mask).sum())
        frame = frame.loc[accepted_mask].copy()
    frame = _coerce_numeric(frame, payload_columns)
    frame, duplicates_collapsed = _collapse_duplicates(frame, payload_columns)
    frame = frame.sort_values("timestamp", kind="stable").reset_index(drop=True)
    audit: dict[str, Any] = {
        "source_path": str(path),
        "source_sha256": _sha256_file(path),
        "raw_rows": raw_rows,
        "accepted_rows": len(frame),
        "duplicates_collapsed": duplicates_collapsed,
        "off_session_rows": off_session_rows,
        "min_accepted_timestamp": str(frame["timestamp"].min()) if not frame.empty else None,
        "max_accepted_timestamp": str(frame["timestamp"].max()) if not frame.empty else None,
        "join_rows": 0,
        "join_coverage": 0.0,
    }
    return frame, audit


def _collapse_duplicates(frame: pd.DataFrame, payload: Iterable[str]) -> tuple[pd.DataFrame, int]:
    payload_columns = tuple(payload)
    duplicate_mask = frame["timestamp"].duplicated(keep=False)
    if not duplicate_mask.any():
        return frame, 0
    duplicates = frame.loc[duplicate_mask]
    for timestamp, group in duplicates.groupby("timestamp", sort=False, dropna=False):
        if group.loc[:, list(payload_columns)].nunique(dropna=False).gt(1).any():
            raise ValueError(f"conflicting duplicate timestamp: {timestamp}")
    collapsed = int(len(duplicates) - duplicates["timestamp"].nunique(dropna=False))
    return frame.drop_duplicates("timestamp", keep="first"), collapsed


def _values_identical(left: pd.Series, right: pd.Series, columns: Iterable[str]) -> bool:
    for column in columns:
        left_value = left.get(column, np.nan)
        right_value = right.get(column, np.nan)
        if pd.isna(left_value) and pd.isna(right_value):
            continue
        if left_value != right_value:
            return False
    return True


def _build_empty_result(index: pd.Index, columns: Iterable[str]) -> pd.DataFrame:
    return pd.DataFrame({column: pd.Series(np.nan, index=index, dtype="float64") for column in columns}, index=index)


def _normalise_decisions(decision_rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    if not isinstance(decision_rows, pd.DataFrame) or decision_rows.empty:
        raise ValueError("decision_rows must be a non-empty DataFrame")
    if "datetime" not in decision_rows:
        raise ValueError("decision_rows requires datetime")
    frame = decision_rows.copy()
    frame["datetime"] = _parse_timestamps(frame["datetime"], dayfirst=False)
    if frame["datetime"].duplicated().any():
        raise ValueError("decision_rows contain duplicate timestamps")
    if "trade_date" in frame:
        dates = _parse_timestamps(frame["trade_date"], dayfirst=False).normalize()
    else:
        dates = frame["datetime"].dt.normalize()
    return frame, dates


def _parse_timestamps(values: pd.Series, *, dayfirst: bool) -> pd.Series:
    try:
        parsed = pd.to_datetime(values, errors="raise", dayfirst=dayfirst, format="mixed")
    except (TypeError, ValueError):
        parsed = pd.to_datetime(values, errors="raise", dayfirst=dayfirst)
    if parsed.dt.tz is not None:
        raise ValueError("timestamps must be naive exchange-local values; timezone conversion is not allowed")
    return parsed


def _parse_cutoff(cutoff: pd.Timestamp | str) -> pd.Timestamp:
    parsed = _parse_timestamps(pd.Series([cutoff]), dayfirst=False).iloc[0]
    return pd.Timestamp(parsed)


def _normalise_mode(mode: str) -> str:
    name = str(mode).strip().upper().replace("_", "-")
    aliases = {"V1B": "V1-B", "V1C": "V1-C", "SPOT-BASIS": "V1-B", "PRODUCTION": "V1-C"}
    name = aliases.get(name, name)
    if name not in {"V1-B", "V1-C"}:
        raise ValueError("mode must be V1-B or V1-C")
    return name


def _series_by_timestamp(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(dtype=float)
    values = pd.to_numeric(frame[column], errors="coerce").to_numpy(dtype=float)
    return pd.Series(values, index=pd.to_datetime(frame["datetime"] if "datetime" in frame else frame["timestamp"]))


def _exact_minute(series: pd.Series, timestamp: pd.Timestamp, trade_date: pd.Timestamp) -> float | None:
    if timestamp.normalize() != pd.Timestamp(trade_date).normalize():
        return None
    value = series.get(timestamp)
    if value is None or pd.isna(value):
        return None
    return float(value)


def _window_values(series: pd.Series, end: pd.Timestamp, window: int, trade_date: pd.Timestamp) -> pd.Series:
    timestamps = pd.date_range(end - pd.Timedelta(minutes=window), end, freq="min")
    if timestamps[0].normalize() != pd.Timestamp(trade_date).normalize() or timestamps[-1].normalize() != pd.Timestamp(trade_date).normalize():
        return pd.Series(dtype=float)
    values = series.reindex(timestamps)
    if values.isna().any():
        return pd.Series(dtype=float)
    return values


def _log_return(series: pd.Series, end: pd.Timestamp, lag: int, trade_date: pd.Timestamp) -> float:
    current = _exact_minute(series, end, trade_date)
    prior = _exact_minute(series, end - pd.Timedelta(minutes=lag), trade_date)
    if current is None or prior is None or current <= 0 or prior <= 0:
        return np.nan
    return float(np.log(current / prior))


def _realized_vol(series: pd.Series, end: pd.Timestamp, window: int, trade_date: pd.Timestamp) -> float:
    prices = _window_values(series, end, window, trade_date)
    if prices.empty or (prices <= 0).any():
        return np.nan
    returns = np.log(prices / prices.shift(1)).dropna()
    if len(returns) < 2:
        return np.nan
    return float(returns.std(ddof=1) * ANNUALIZED_MINUTES)


def _set_join_audit(audit: dict[str, Any], matched: int, total: int) -> None:
    audit["join_rows"] = int(matched)
    audit["join_coverage"] = float(matched / total) if total else 0.0


def _coerce_numeric(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    result = frame.copy()
    for column in columns:
        if column in result:
            result[column] = pd.to_numeric(result[column], errors="raise")
    return result


def _require_columns(frame: pd.DataFrame, required: set[str], source: str) -> None:
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"{source} source requires {', '.join(missing)}")


def _sha256_file(path: str | Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
