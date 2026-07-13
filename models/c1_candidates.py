"""Sleeve F C1 candidates, deterministic training, and candidate B calibration."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from features.sleeve_f_router import FUTURES_FEATURES


PROTOCOL_VERSION = "sleeve-f-c1-v1"

# These are the registered parameters.  Reproducibility controls and the
# fold-derived seeds are added only when constructing the LightGBM estimator.
A_PARAMS: dict[str, Any] = {
    "objective": "binary",
    "n_estimators": 500,
    "learning_rate": 0.05,
    "num_leaves": 63,
    "min_child_samples": 200,
    "feature_fraction": 0.85,
    "bagging_fraction": 0.85,
    "bagging_freq": 5,
    "reg_alpha": 0.1,
    "reg_lambda": 1.0,
    "is_unbalance": True,
}

C_PARAMS: dict[str, Any] = {
    "objective": "huber",
    "n_estimators": 200,
    "max_depth": 2,
    "num_leaves": 4,
    "learning_rate": 0.03,
    "min_child_samples": 100,
    "feature_fraction": 0.7,
    "bagging_fraction": 0.7,
    "reg_alpha": 1.0,
    "reg_lambda": 10.0,
}

B_TRAILING_SESSIONS = 60
B_TERCILE = 2.0 / 3.0


@dataclass(frozen=True)
class CandidateTrainingResult:
    """A trained fold model and its frozen audit metadata."""

    model: Any
    metadata: dict[str, Any]

    def __iter__(self):
        yield self.model
        yield self.metadata


def derive_seed(protocol_version: str, candidate: str, fold: str | int) -> int:
    """Derive the registered uint32 seed from protocol, candidate, and fold."""
    material = f"{protocol_version}|{candidate}|{fold}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(material).digest()[:4], "big", signed=False)


def params_hash(params: Mapping[str, Any]) -> str:
    """Hash a parameter mapping without relying on dictionary insertion order."""
    encoded = json.dumps(dict(params), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def train_candidate(
    candidate: str,
    purged_train: pd.DataFrame,
    inner_validation: pd.DataFrame,
    *,
    fold: str | int,
    protocol_version: str = PROTOCOL_VERSION,
    feature_columns: Sequence[str] = FUTURES_FEATURES,
) -> CandidateTrainingResult:
    """Train A or C on already-purged train/validation frames."""
    normalized = candidate.upper()
    if normalized not in {"A", "C"}:
        raise ValueError("train_candidate supports only candidates A and C")
    if not isinstance(purged_train, pd.DataFrame) or purged_train.empty:
        raise ValueError("purged_train must be a non-empty DataFrame")
    if not isinstance(inner_validation, pd.DataFrame) or inner_validation.empty:
        raise ValueError("inner_validation must be a non-empty DataFrame")

    columns = tuple(feature_columns)
    label_column = "net_label" if normalized == "A" else "net_return_r"
    params = dict(A_PARAMS if normalized == "A" else C_PARAMS)
    x_train, y_train = _training_arrays(purged_train, columns, label_column)
    x_validation, y_validation = _training_arrays(inner_validation, columns, label_column)
    if normalized == "A" and y_train.nunique() < 2:
        raise ValueError("candidate A training labels must contain both classes")
    if normalized == "A" and y_validation.nunique() < 2:
        raise ValueError("candidate A validation labels must contain both classes")

    seed = derive_seed(protocol_version, normalized, fold)
    estimator_kwargs = {
        **params,
        "random_state": seed,
        "deterministic": True,
        "force_row_wise": True,
        "feature_fraction_seed": seed,
        "bagging_seed": seed,
        "data_random_seed": seed,
        "n_jobs": 1,
        "verbosity": -1,
    }
    import lightgbm as lgb

    # Registration §3 gives early stopping (30 rounds on purged inner
    # validation) to candidate A only; C is a fixed 200-tree fit.
    if normalized == "A":
        model = lgb.LGBMClassifier(**estimator_kwargs)
        eval_metric = "binary_logloss"
        callbacks = [lgb.early_stopping(30, first_metric_only=True, verbose=False)]
    else:
        model = lgb.LGBMRegressor(**estimator_kwargs)
        eval_metric = "huber"
        callbacks = []
    model.fit(
        x_train,
        y_train,
        eval_set=[(x_validation, y_validation)],
        eval_names=["inner_validation"],
        eval_metric=eval_metric,
        callbacks=callbacks,
    )
    metadata = {
        "candidate": normalized,
        "fold": str(fold),
        "protocol_version": protocol_version,
        "best_iteration": int(model.best_iteration_ or model.n_estimators),
        "seed": seed,
        "params_hash": params_hash(params),
        "params": params,
        "feature_columns": list(columns),
        "label_column": label_column,
    }
    return CandidateTrainingResult(model=model, metadata=metadata)


def train_a(
    purged_train: pd.DataFrame,
    inner_validation: pd.DataFrame,
    *,
    fold: str | int,
    protocol_version: str = PROTOCOL_VERSION,
    feature_columns: Sequence[str] = FUTURES_FEATURES,
) -> CandidateTrainingResult:
    return train_candidate(
        "A", purged_train, inner_validation, fold=fold,
        protocol_version=protocol_version, feature_columns=feature_columns,
    )


def train_c(
    purged_train: pd.DataFrame,
    inner_validation: pd.DataFrame,
    *,
    fold: str | int,
    protocol_version: str = PROTOCOL_VERSION,
    feature_columns: Sequence[str] = FUTURES_FEATURES,
) -> CandidateTrainingResult:
    return train_candidate(
        "C", purged_train, inner_validation, fold=fold,
        protocol_version=protocol_version, feature_columns=feature_columns,
    )


def predict_scores(model: Any, rows: pd.DataFrame, candidate: str, *, feature_columns: Sequence[str] = FUTURES_FEATURES) -> np.ndarray:
    """Return the score consumed by replay for a trained A or C model."""
    x = rows.loc[:, list(feature_columns)]
    if candidate.upper() == "A":
        return np.asarray(model.predict_proba(x)[:, 1], dtype=float)
    if candidate.upper() == "C":
        return np.asarray(model.predict(x), dtype=float)
    raise ValueError("predict_scores supports only candidates A and C")


def c_effective_threshold(fitted_threshold: float) -> float:
    """Return C's threshold floor, representing the registered ``0+``."""
    threshold = float(fitted_threshold)
    return max(threshold, float(np.nextafter(0.0, 1.0)))


def c_trade_mask(predicted_net_r: Iterable[float], fitted_threshold: float) -> np.ndarray:
    """Apply C's positive-net-R rule and the fitted activity threshold."""
    predictions = np.asarray(list(predicted_net_r), dtype=float)
    threshold = float(fitted_threshold)
    if threshold > 0.0:
        return np.isfinite(predictions) & (predictions >= threshold)
    return np.isfinite(predictions) & (predictions > 0.0)


def c_scores(predicted_net_r: Iterable[float]) -> np.ndarray:
    """Make replay scores, using NaN for C predictions that cannot trade."""
    predictions = np.asarray(list(predicted_net_r), dtype=float)
    return np.where(np.isfinite(predictions) & (predictions > 0.0), predictions, np.nan)


def calibrate_b(
    rows: pd.DataFrame,
    *,
    windows: Sequence[Sequence[int] | Mapping[str, int] | str] | None = None,
) -> dict[str, Any]:
    """Build B's deterministic constants artifact from 2020 rows only.

    ``windows`` is deliberately explicit because the registration references
    pre-registered minute windows but does not publish their values.  The
    artifact stores the supplied windows exactly in normalized minute form.
    Volatility decisions remain causal: each session is compared with the
    2/3 quantile of values from only its preceding 60 sessions.
    """
    frame = _normalise_b_rows(rows)
    dates = sorted(frame["trade_date"].dt.date.unique())
    if not dates or min(dates) < date(2020, 1, 1) or max(dates) >= date(2021, 1, 1):
        raise ValueError("candidate B calibration accepts strictly 2020 data only")
    normalized_windows = _normalize_windows(windows)
    thresholds = _causal_thresholds(frame)
    if not thresholds:
        raise ValueError("candidate B calibration needs at least two sessions")
    return {
        "candidate": "B",
        "feature": "realized_vol_30m",
        "trailing_sessions": B_TRAILING_SESSIONS,
        "tercile": B_TERCILE,
        "windows": [list(window) for window in normalized_windows],
        "calibration_year": 2020,
        "calibration_sessions": len(dates),
        "causal_threshold_median": float(np.median(thresholds)),
    }


def b_scores(rows: pd.DataFrame, artifact: Mapping[str, Any]) -> np.ndarray:
    """Return B's replay representation: ``1.0`` for a rule fire, NaN else."""
    frame = _normalise_b_rows(rows)
    windows = _normalize_windows(artifact.get("windows"))
    result = np.full(len(frame), np.nan, dtype=float)
    dates = sorted(frame["trade_date"].dt.date.unique())
    positions = {day: index for index, day in enumerate(dates)}
    values = pd.to_numeric(frame["realized_vol_30m"], errors="coerce")
    for day, day_index in positions.items():
        prior_days = dates[max(0, day_index - B_TRAILING_SESSIONS):day_index]
        if not prior_days:
            continue
        prior = values[frame["trade_date"].dt.date.isin(prior_days)].dropna()
        if prior.empty:
            continue
        threshold = float(prior.quantile(B_TERCILE))
        day_mask = frame["trade_date"].dt.date.eq(day)
        minute = frame["minute_of_day"]
        in_window = pd.Series(False, index=frame.index)
        for start, end in windows:
            in_window |= (minute >= start) & (minute < end)
        fires = day_mask & values.ge(threshold) & in_window
        result[fires.to_numpy()] = 1.0
    return result


def b_trade_mask(rows: pd.DataFrame, artifact: Mapping[str, Any]) -> np.ndarray:
    """Return B's binary rule output; B bypasses threshold fitting."""
    return np.isfinite(b_scores(rows, artifact))


def write_b_artifact(artifact: Mapping[str, Any], output: str | Path) -> None:
    """Write a canonical, timestamp-free B constants JSON artifact."""
    path = Path(output)
    path.write_text(
        json.dumps(dict(artifact), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the frozen C1 candidate B constants artifact")
    parser.add_argument("input", type=Path, help="CSV containing 2020 rows")
    parser.add_argument("output", type=Path, help="JSON artifact path")
    parser.add_argument("--windows", required=True, help="JSON minute windows, e.g. [[570,630],[780,870]]")
    args = parser.parse_args(argv)
    rows = pd.read_csv(args.input)
    artifact = calibrate_b(rows, windows=json.loads(args.windows))
    write_b_artifact(artifact, args.output)
    return 0


def _training_arrays(
    frame: pd.DataFrame,
    feature_columns: Sequence[str],
    label_column: str,
) -> tuple[pd.DataFrame, pd.Series]:
    missing = sorted(set(feature_columns) - set(frame.columns))
    if label_column not in frame:
        raise ValueError(f"training frame needs {label_column!r}")
    if missing:
        raise ValueError(f"training frame missing features: {', '.join(missing)}")
    valid = frame[label_column].notna()
    if not valid.any():
        raise ValueError(f"training frame has no non-null {label_column} labels")
    return frame.loc[valid, list(feature_columns)].copy(), frame.loc[valid, label_column].copy()


def _normalise_b_rows(rows: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(rows, pd.DataFrame) or rows.empty:
        raise ValueError("B rows must be a non-empty DataFrame")
    required = {"realized_vol_30m", "minute_of_day"}
    missing = sorted(required - set(rows.columns))
    if missing:
        raise ValueError(f"B rows missing columns: {', '.join(missing)}")
    frame = rows.copy()
    if "trade_date" in frame:
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise").dt.normalize()
    elif "datetime" in frame:
        frame["datetime"] = pd.to_datetime(frame["datetime"], errors="raise")
        frame["trade_date"] = frame["datetime"].dt.normalize()
    else:
        raise ValueError("B rows need trade_date or datetime")
    frame["realized_vol_30m"] = pd.to_numeric(frame["realized_vol_30m"], errors="coerce")
    frame["minute_of_day"] = pd.to_numeric(frame["minute_of_day"], errors="raise").astype(int)
    return frame


def _causal_thresholds(frame: pd.DataFrame) -> list[float]:
    dates = sorted(frame["trade_date"].dt.date.unique())
    values = frame["realized_vol_30m"]
    thresholds: list[float] = []
    for index in range(1, len(dates)):
        prior_days = dates[max(0, index - B_TRAILING_SESSIONS):index]
        prior = values[frame["trade_date"].dt.date.isin(prior_days)].dropna()
        if not prior.empty:
            thresholds.append(float(prior.quantile(B_TERCILE)))
    return thresholds


def _normalize_windows(
    windows: Sequence[Sequence[int] | Mapping[str, int] | str] | None,
) -> tuple[tuple[int, int], ...]:
    if windows is None:
        raise ValueError("pre-registered B minute windows must be supplied explicitly")
    normalized: list[tuple[int, int]] = []
    for window in windows:
        if isinstance(window, str):
            parts = window.replace("–", "-").split("-")
            if len(parts) != 2:
                raise ValueError(f"invalid B minute window: {window!r}")
            start, end = (_minute(value.strip()) for value in parts)
        elif isinstance(window, Mapping):
            start, end = int(window["start"]), int(window["end"])
        else:
            if len(window) != 2:
                raise ValueError("B minute windows need start and end")
            start, end = int(window[0]), int(window[1])
        if not (0 <= start < end <= 24 * 60):
            raise ValueError("B minute windows must satisfy 0 <= start < end <= 1440")
        normalized.append((start, end))
    if not normalized:
        raise ValueError("at least one B minute window is required")
    return tuple(sorted(set(normalized)))


def _minute(value: str) -> int:
    if ":" in value:
        hour, minute = (int(part) for part in value.split(":", 1))
        return hour * 60 + minute
    return int(value)


if __name__ == "__main__":
    raise SystemExit(main())
