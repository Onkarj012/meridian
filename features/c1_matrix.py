"""Deterministic Campaign 1 feature/label matrix assembly.

This module deliberately keeps all ordinary-session rows.  Eligibility is a
set of columns for the replay/training consumers to apply; only sessions that
the frozen registration explicitly drops are absent from the matrix.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from contracts.sleeve_f_labels import generate_execution_consistent_labels
from features.causal_regime import add_regime_causal, regime_config_hash
from features.sleeve_f_router import FUTURES_FEATURES, compute_features
from features.sleeve_f_signals import _load_spot_for_date
from policy.era_costs import cost_bps_fn_for, era_table_hash


REGULAR_START_MINUTE = 9 * 60 + 15
REGULAR_END_MINUTE = 15 * 60 + 29
WARMUP_START_MINUTE = 9 * 60 + 45
DECISION_END_MINUTE = 14 * 60 + 54
EXCLUDED_TIME_START_MINUTE = 11 * 60
EXCLUDED_TIME_END_MINUTE = 12 * 60
HORIZON_BARS = 60
HISTORY_SESSIONS = 60

MUHURAT_DATES = frozenset(pd.Timestamp(value) for value in (
    "2020-11-14", "2021-11-04", "2022-10-24", "2023-11-12",
))
DROPPED_DATES = frozenset(pd.Timestamp(value) for value in (
    "2021-02-24", "2025-09-26", "2026-06-03",
))
CLASS_A_FEATURES = (
    "ret_1m", "ret_5m", "ret_15m", "ret_30m", "ret_60m",
    "log_ret_1m", "log_ret_5m", "log_ret_15m", "log_ret_30m",
    "atr_5m", "atr_15m", "atr_30m",
    "realized_vol_5m", "realized_vol_15m", "realized_vol_30m",
    "vwap_dev", "vwap_slope_5m", "or_dist_high", "or_dist_low",
    "or_breakout_up", "or_breakout_dn", "minute_of_day", "hour_of_day",
    "session_progress", "day_of_week", "gap_pct", "consec_bars", "ema_slope",
)
ELIGIBILITY_COLUMNS = (
    "is_session_eligible", "is_time_eligible", "is_warmup_eligible",
    "class_a_eligible", "regime_eligible", "label_window_contiguous",
    "label_excluded", "is_decision_eligible",
)
LABEL_COLUMNS = (
    "decision_close", "entry_price", "exit_price", "exit_reason", "exit_bar_offset",
    "gross_return_bps", "gross_return_r", "mfe_bps", "mae_bps", "cost_bps",
    "net_return_bps", "net_return_r", "net_label",
)
BAR_COLUMNS = ("f_open", "f_high", "f_low", "f_close", "f_vol", "f_oi")
MATRIX_COLUMNS = ("datetime", "trade_date", *BAR_COLUMNS, *FUTURES_FEATURES, "regime", *LABEL_COLUMNS,
                  "label_end_ts", *ELIGIBILITY_COLUMNS)


def c1_matrix_config_hash() -> str:
    """Return the canonical hash of C1 matrix mechanics owned by this module."""
    config = {
        "class_a_features": CLASS_A_FEATURES,
        "decision_window": "09:45..14:54 inclusive, excluding 11:00..11:59",
        "dropped_dates": sorted(str(value.date()) for value in DROPPED_DATES | MUHURAT_DATES),
        "bar_columns": BAR_COLUMNS,
        "feature_columns": FUTURES_FEATURES,
        "horizon_bars": HORIZON_BARS,
        "regular_session": "09:15..15:29 inclusive",
        "warmup_start": "09:45",
    }
    encoded = json.dumps(config, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def archive_path(root: Path, session: pd.Timestamp) -> Path:
    """Return the registered archive location for one calendar day."""
    return root / str(session.year) / str(session.month) / f"nifty_fut_{session:%d_%m_%Y}.csv"


def archive_dates(root: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    """Return available archive sessions in deterministic calendar order."""
    return pd.DatetimeIndex(day for day in pd.date_range(start, end, freq="D") if archive_path(root, day).exists())


def load_archive_sessions(root: Path, dates: Iterable[pd.Timestamp]) -> Iterable[pd.DataFrame]:
    """Yield raw archive files one session at a time."""
    for session in dates:
        yield pd.read_csv(archive_path(root, pd.Timestamp(session)), usecols=[
            "date", "time", "open", "high", "low", "close", "oi", "volume",
        ])


def build_c1_matrix(
    sessions: Iterable[pd.DataFrame],
    *,
    data_root: Path | None = None,
    spot_source: str = "auto",
    spot_index_path: Path | None = None,
    spot_loader: Callable[[str], Mapping[str, float]] | None = None,
) -> pd.DataFrame:
    """Assemble C1 rows from chronologically ordered, per-session minute bars.

    ``spot_loader`` is a narrow synthetic-test seam.  Production callers leave
    it unset, which invokes the frozen real-spot loader for every session.
    """
    root = Path(data_root) if data_root is not None else None
    index_path = spot_index_path or (root / "nifty_intraday" / "NIFTY 50_minute.csv" if root else None)
    history: list[pd.DataFrame] = []
    output: list[pd.DataFrame] = []
    prior_session: pd.Timestamp | None = None

    for raw in sessions:
        day = _normalise_session(raw)
        if day.empty:
            continue
        session = pd.Timestamp(day["trade_date"].iat[0]).normalize()
        if prior_session is not None and session <= prior_session:
            raise ValueError("C1 sessions must arrive in strictly increasing trade_date order")
        prior_session = session
        if session in DROPPED_DATES or session in MUHURAT_DATES:
            continue

        if "s_close" not in day:
            date_key = session.strftime("%Y-%m-%d")
            if spot_loader is not None:
                spot = spot_loader(date_key)
            elif root is not None and index_path is not None:
                spot, _source = _load_spot_for_date(
                    date_key, data_root=root, spot_source=spot_source, index_file_path=index_path,
                )
            else:
                raise ValueError("data_root is required when sessions do not provide s_close")
            day["s_close"] = [spot.get(timestamp.strftime("%Y-%m-%d %H:%M:00"), np.nan) for timestamp in day["datetime"]]

        features = compute_features(day, session.date())
        features["trade_date"] = session
        labels = generate_execution_consistent_labels(day, cost_bps_fn_for())
        frame = features.merge(labels.drop(columns="trade_date"), on="datetime", how="left", validate="one_to_one")
        frame["label_window_contiguous"] = _contiguous_label_windows(frame["datetime"]).astype(bool)
        frame.loc[~frame["label_window_contiguous"], "label_excluded"] = True
        history.append(frame)
        causal = add_regime_causal(pd.concat(history, ignore_index=True))
        current = causal.iloc[-len(frame):].copy()
        current["label_end_ts"] = _label_end_timestamps(current)
        current = _add_eligibility(current)
        output.append(current.reindex(columns=MATRIX_COLUMNS))
        if len(history) > HISTORY_SESSIONS:
            history.pop(0)

    if not output:
        return _empty_matrix()
    result = pd.concat(output, ignore_index=True)
    result["datetime"] = pd.to_datetime(result["datetime"])
    result["trade_date"] = pd.to_datetime(result["trade_date"])
    result["label_end_ts"] = pd.to_datetime(result["label_end_ts"])
    return result.sort_values("datetime", kind="stable").reset_index(drop=True)


def _normalise_session(raw: pd.DataFrame) -> pd.DataFrame:
    frame = raw.copy()
    if "datetime" not in frame:
        if not {"date", "time"}.issubset(frame.columns):
            raise ValueError("C1 bars need datetime or date/time columns")
        frame["datetime"] = pd.to_datetime(frame["date"].astype(str) + " " + frame["time"].astype(str))
    else:
        frame["datetime"] = pd.to_datetime(frame["datetime"])
    rename = {"open": "f_open", "high": "f_high", "low": "f_low", "close": "f_close", "volume": "f_vol", "oi": "f_oi"}
    frame = frame.rename(columns={key: value for key, value in rename.items() if key in frame and value not in frame})
    required = {"f_open", "f_high", "f_low", "f_close", "f_vol", "f_oi"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"C1 requires real archive fields: {', '.join(missing)}")
    for column in required - {"datetime"}:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame["trade_date"] = frame["datetime"].dt.normalize()
    if frame["trade_date"].nunique() > 1:
        raise ValueError("C1 session input contains more than one trade date")
    minute = frame["datetime"].dt.hour * 60 + frame["datetime"].dt.minute
    return frame.loc[(minute >= REGULAR_START_MINUTE) & (minute <= REGULAR_END_MINUTE)].sort_values(
        "datetime", kind="stable"
    ).reset_index(drop=True)


def _contiguous_label_windows(datetimes: pd.Series) -> np.ndarray:
    values = pd.to_datetime(datetimes).to_numpy(dtype="datetime64[m]")
    contiguous = np.zeros(len(values), dtype=bool)
    for decision in range(len(values)):
        if decision + 1 >= len(values):
            continue
        last = min(len(values) - 1, decision + HORIZON_BARS)
        differences = np.diff(values[decision + 1:last + 1]).astype("timedelta64[m]").astype(int)
        contiguous[decision] = bool(np.all(differences == 1))
    return contiguous


def _label_end_timestamps(frame: pd.DataFrame) -> pd.Series:
    timestamps = pd.to_datetime(frame["datetime"])
    offsets = pd.to_numeric(frame["exit_bar_offset"], errors="coerce")
    excluded = frame["label_excluded"].fillna(True).astype(bool)
    result = pd.Series(pd.NaT, index=frame.index, dtype="datetime64[ns]")
    valid = ~excluded & offsets.notna() & (offsets >= 1)
    # exit_bar_offset is decision-relative in the frozen label contract.  It
    # is equivalently entry + (offset - 1) minutes, because entry is t + 1.
    result.loc[valid] = (timestamps.loc[valid] + pd.to_timedelta(offsets.loc[valid], unit="min")).to_numpy()
    return result


def _add_eligibility(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    minute = result["minute_of_day"].astype(int)
    result["is_session_eligible"] = True
    result["is_warmup_eligible"] = (minute >= WARMUP_START_MINUTE)
    result["is_time_eligible"] = (
        (minute >= WARMUP_START_MINUTE) & (minute <= DECISION_END_MINUTE)
        & ~((minute >= EXCLUDED_TIME_START_MINUTE) & (minute < EXCLUDED_TIME_END_MINUTE))
    )
    result["class_a_eligible"] = ~result.loc[:, CLASS_A_FEATURES].isna().any(axis=1)
    result["label_excluded"] = result["label_excluded"].fillna(True).astype(bool)
    result["label_window_contiguous"] = result["label_window_contiguous"].astype(bool)
    result["regime_eligible"] = result["regime_eligible"].astype(bool)
    result["is_decision_eligible"] = (
        result["is_session_eligible"] & result["is_time_eligible"] & result["is_warmup_eligible"]
        & result["class_a_eligible"] & result["regime_eligible"]
        & result["label_window_contiguous"] & ~result["label_excluded"]
        & (result["regime"] != "compression")
    )
    return result


def _empty_matrix() -> pd.DataFrame:
    data: dict[str, pd.Series] = {}
    for column in MATRIX_COLUMNS:
        if column in {"datetime", "trade_date", "label_end_ts"}:
            data[column] = pd.Series(dtype="datetime64[ns]")
        elif column in {"regime", "exit_reason"}:
            data[column] = pd.Series(dtype="object")
        elif column in BAR_COLUMNS:
            data[column] = pd.Series(dtype="float64")
        elif column in ELIGIBILITY_COLUMNS:
            data[column] = pd.Series(dtype="bool")
        else:
            data[column] = pd.Series(dtype="float64")
    return pd.DataFrame(data)


def artifact_report(matrix: pd.DataFrame, parquet_sha256: str) -> dict[str, object]:
    """Return deterministic metadata for the matrix artifact."""
    return {
        "c1": {
            "columns": list(matrix.columns),
            "config_hash": c1_matrix_config_hash(),
            "era_table_hash": era_table_hash(),
            "regime_config_hash": regime_config_hash(),
            "row_count": int(len(matrix)),
            "sha256": parquet_sha256,
        }
    }
