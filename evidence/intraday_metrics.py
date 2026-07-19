"""Dependence-aware direction, magnitude, confidence, and baseline metrics."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score, balanced_accuracy_score, confusion_matrix, f1_score,
    mean_absolute_error, mean_squared_error, precision_recall_fscore_support,
    r2_score, roc_auc_score,
)


CLASS_ORDER = ("DOWN", "FLAT", "UP")


def direction_metrics(y_true: Iterable[Any], probabilities: pd.DataFrame | np.ndarray) -> dict[str, Any]:
    truth = _labels(y_true)
    probs = _probs(probabilities)
    predicted = np.asarray(CLASS_ORDER)[probs.argmax(axis=1)]
    result: dict[str, Any] = {
        "accuracy": float(accuracy_score(truth, predicted)),
        "balanced_accuracy": float(balanced_accuracy_score(truth, predicted)),
        "macro_f1": float(f1_score(truth, predicted, labels=CLASS_ORDER, average="macro", zero_division=0)),
        "confusion_matrix": confusion_matrix(truth, predicted, labels=CLASS_ORDER).tolist(),
    }
    precision, recall, _, support = precision_recall_fscore_support(truth, predicted, labels=CLASS_ORDER, zero_division=0)
    result["per_class"] = {
        label: {"precision": float(precision[index]), "recall": float(recall[index]), "support": int(support[index])}
        for index, label in enumerate(CLASS_ORDER)
    }
    try:
        result["macro_ovr_auc"] = float(roc_auc_score(truth, probs, labels=CLASS_ORDER, multi_class="ovr", average="macro"))
    except ValueError:
        result["macro_ovr_auc"] = None
    non_flat = np.isin(truth, ["DOWN", "UP"])
    if non_flat.any() and len(np.unique(truth[non_flat])) == 2:
        result["up_vs_down_auc"] = float(roc_auc_score((truth[non_flat] == "UP").astype(int), probs[non_flat, 2] / np.maximum(probs[non_flat, 0] + probs[non_flat, 2], 1e-12)))
    else:
        result["up_vs_down_auc"] = None
    return result


def magnitude_metrics(
    y_true: Iterable[float],
    prediction: Iterable[float],
    *,
    timestamps: Iterable[Any] | None = None,
    regimes: Iterable[Any] | None = None,
) -> dict[str, Any]:
    actual = np.asarray(list(y_true), dtype=float)
    predicted = np.asarray(list(prediction), dtype=float)
    valid = np.isfinite(actual) & np.isfinite(predicted)
    actual, predicted = actual[valid], predicted[valid]
    result = {
        "mae_bps": float(mean_absolute_error(actual, predicted)) if len(actual) else None,
        "rmse_bps": float(np.sqrt(mean_squared_error(actual, predicted))) if len(actual) else None,
        "r2": float(r2_score(actual, predicted)) if len(actual) > 1 else None,
        "pearson": _pearson(actual, predicted),
    }
    if timestamps is not None:
        ts = pd.to_datetime(pd.Series(list(timestamps))).iloc[np.flatnonzero(valid)].reset_index(drop=True)
        result["daily_spearman_ic"] = _daily_ic(actual, predicted, ts)
        result["ic_slices"] = _ic_slices(actual, predicted, ts, regimes)
    else:
        result["daily_spearman_ic"] = {"count": 0, "mean": None, "median": None, "positive_fraction": None, "values": {}}
        result["ic_slices"] = {}
    return result


def confidence_metrics(correctness: Iterable[int], confidence: Iterable[float], bins: int = 10) -> dict[str, Any]:
    """Calculate binary Brier, equal-frequency ECE, and reliability data."""
    y = np.asarray(list(correctness), dtype=float)
    p = np.asarray(list(confidence), dtype=float)
    valid = np.isfinite(y) & np.isfinite(p)
    y, p = y[valid], np.clip(p[valid], 0, 1)
    if not len(y):
        return {"brier": None, "ece": None, "reliability_curve": [], "calibration_slope": None, "calibration_intercept": None, "accuracy_by_confidence_decile": {}}
    edges = np.unique(np.quantile(p, np.linspace(0, 1, min(bins, len(p)) + 1)))
    bin_ids = np.searchsorted(edges, p, side="right") - 1
    bin_ids = np.clip(bin_ids, 0, max(len(edges) - 2, 0))
    reliability = []
    for index in range(max(len(edges) - 1, 1)):
        selected = bin_ids == index
        if selected.any():
            reliability.append({"bin": index, "count": int(selected.sum()), "mean_confidence": float(p[selected].mean()), "empirical_accuracy": float(y[selected].mean())})
    ece = sum(item["count"] / len(y) * abs(item["mean_confidence"] - item["empirical_accuracy"]) for item in reliability)
    slope, intercept = _calibration_line(y, p)
    deciles = {str(index + 1): float(y[order].mean()) for index, order in enumerate(_equal_frequency_indices(p, 10)) if len(order)}
    return {"brier": float(np.mean((p - y) ** 2)), "ece": float(ece), "reliability_curve": reliability, "calibration_slope": slope, "calibration_intercept": intercept, "accuracy_by_confidence_decile": deciles}


def multiclass_brier(y_true: Iterable[Any], probabilities: pd.DataFrame | np.ndarray) -> float:
    truth = _labels(y_true)
    probs = _probs(probabilities)
    one_hot = np.column_stack([(truth == label).astype(float) for label in CLASS_ORDER])
    return float(np.mean(np.sum((probs - one_hot) ** 2, axis=1)))


def session_block_bootstrap(
    values: Iterable[float] | pd.DataFrame,
    sessions: Iterable[Any] | None = None,
    statistic: Callable[[np.ndarray], float] = np.mean,
    *,
    n_bootstrap: int = 2_000,
    seed: int = 20260718,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """Bootstrap complete sessions, preserving all rows within sampled days."""
    if isinstance(values, pd.DataFrame):
        frame = values.copy()
        value_column = "value" if "value" in frame else frame.select_dtypes(include=[np.number]).columns[0]
        values_array = frame[value_column].to_numpy(dtype=float)
        sessions_array = frame["trade_date"].to_numpy() if sessions is None else np.asarray(list(sessions))
    else:
        values_array = np.asarray(list(values), dtype=float)
        if sessions is None:
            raise ValueError("sessions are required for session-block bootstrap")
        sessions_array = np.asarray(list(sessions))
    unique = pd.unique(sessions_array)
    if len(values_array) != len(sessions_array) or not len(unique):
        raise ValueError("values and sessions must align and be non-empty")
    grouped = [np.flatnonzero(sessions_array == session) for session in unique]
    rng = np.random.default_rng(seed)
    draws = np.empty(int(n_bootstrap), dtype=float)
    for index in range(len(draws)):
        sampled = rng.integers(0, len(grouped), size=len(grouped))
        sample = np.concatenate([values_array[grouped[group]] for group in sampled])
        draws[index] = float(statistic(sample))
    alpha = (1 - confidence) / 2
    return float(np.quantile(draws, alpha)), float(np.quantile(draws, 1 - alpha))


def paired_session_block_bootstrap(
    candidate: Iterable[float], baseline: Iterable[float], sessions: Iterable[Any], *,
    n_bootstrap: int = 2_000, seed: int = 20260718, confidence: float = 0.95,
) -> tuple[float, float]:
    differences = np.asarray(list(candidate), dtype=float) - np.asarray(list(baseline), dtype=float)
    return session_block_bootstrap(differences, sessions, n_bootstrap=n_bootstrap, seed=seed, confidence=confidence)


def bootstrap_ci(*args: Any, **kwargs: Any) -> tuple[float, float]:
    return session_block_bootstrap(*args, **kwargs)


def naive_baselines(frame: pd.DataFrame, horizon: int, *, training_mean: float | None = None) -> dict[str, pd.DataFrame]:
    """Return the required hard-label and zero/mean magnitude baselines."""
    result = frame.copy()
    close = pd.to_numeric(result.get("log_ret_5m", 0), errors="coerce").fillna(0)
    actual_mean = float(training_mean if training_mean is not None else pd.to_numeric(result[f"target_h{horizon}_mag_bps"], errors="coerce").mean())
    def prediction(direction: pd.Series, magnitude: float | pd.Series) -> pd.DataFrame:
        output = pd.DataFrame({"direction": direction.astype(str), "expected_log_return_bps": magnitude}, index=result.index)
        output["p_down_raw"] = (output["direction"] == "DOWN").astype(float)
        output["p_flat_raw"] = (output["direction"] == "FLAT").astype(float)
        output["p_up_raw"] = (output["direction"] == "UP").astype(float)
        return output
    return {
        "always_up": prediction(pd.Series("UP", index=result.index), 0.0),
        "last_5m_sign": prediction(pd.Series(np.where(close > 0, "UP", "DOWN"), index=result.index), 0.0),
        "zero_return": prediction(pd.Series("FLAT", index=result.index), 0.0),
        "training_mean": prediction(pd.Series("FLAT", index=result.index), actual_mean),
    }


def non_overlapping_decisions(frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Select decisions anchored at 09:30 and then every H minutes."""
    if "datetime" not in frame:
        raise ValueError("frame requires datetime")
    result = frame.copy()
    timestamps = pd.to_datetime(result["datetime"])
    minutes = timestamps.dt.hour * 60 + timestamps.dt.minute
    start = timestamps.dt.normalize() + pd.Timedelta(minutes=570)
    offsets = ((timestamps - start).dt.total_seconds() / 60).round()
    return result.loc[(minutes >= 570) & (offsets >= 0) & (offsets % horizon == 0)].copy()


def robustness_table(frame: pd.DataFrame, horizon: int) -> dict[str, Any]:
    subset = non_overlapping_decisions(frame, horizon)
    return {"horizon": int(horizon), "rows": int(len(subset)), "sessions": int(pd.to_datetime(subset["datetime"]).dt.normalize().nunique()) if len(subset) else 0}


def _labels(value: Iterable[Any]) -> np.ndarray:
    array = np.asarray(list(value))
    return np.asarray(pd.Categorical(array, categories=CLASS_ORDER), dtype=object)


def _probs(value: pd.DataFrame | np.ndarray) -> np.ndarray:
    if isinstance(value, pd.DataFrame) and {"p_down_raw", "p_flat_raw", "p_up_raw"}.issubset(value.columns):
        value = value[["p_down_raw", "p_flat_raw", "p_up_raw"]]
    array = np.asarray(value, dtype=float)
    if array.ndim != 2 or array.shape[1] != 3:
        raise ValueError("probabilities must have three columns")
    return array


def _daily_ic(actual: np.ndarray, predicted: np.ndarray, timestamps: pd.Series) -> dict[str, Any]:
    values: dict[str, float] = {}
    for day, group in pd.DataFrame({"actual": actual, "predicted": predicted, "day": timestamps.dt.normalize()}).groupby("day"):
        if len(group) >= 30 and group["actual"].nunique() > 1 and group["predicted"].nunique() > 1:
            values[str(day.date())] = _spearman(group["actual"], group["predicted"])
    series = np.asarray(list(values.values()), dtype=float)
    return {"count": int(len(series)), "mean": float(series.mean()) if len(series) else None, "median": float(np.median(series)) if len(series) else None, "positive_fraction": float((series > 0).mean()) if len(series) else None, "values": values}


def _ic_slices(actual: np.ndarray, predicted: np.ndarray, timestamps: pd.Series, regimes: Iterable[Any] | None) -> dict[str, Any]:
    frame = pd.DataFrame({"actual": actual, "predicted": predicted, "timestamp": timestamps})
    frame["quarter"] = frame["timestamp"].dt.to_period("Q").astype(str)
    minute = frame["timestamp"].dt.hour * 60 + frame["timestamp"].dt.minute
    frame["time_of_day"] = pd.cut(minute, [569, 690, 810, 930], labels=["open", "midday", "close"])
    frame["regime"] = list(regimes)[:len(frame)] if regimes is not None else "unknown"
    result = {}
    for column in ("quarter", "time_of_day", "regime"):
        result[column] = {}
        for key, group in frame.groupby(column, dropna=False):
            result[column][str(key)] = _single_ic(group["actual"], group["predicted"])
    return result


def _single_ic(actual: Iterable[float], predicted: Iterable[float]) -> float | None:
    a, p = np.asarray(list(actual)), np.asarray(list(predicted))
    return _spearman(a, p) if len(a) > 1 and len(np.unique(a)) > 1 and len(np.unique(p)) > 1 else None


def _pearson(actual: Iterable[float], predicted: Iterable[float]) -> float | None:
    a, p = np.asarray(list(actual), dtype=float), np.asarray(list(predicted), dtype=float)
    if len(a) < 2 or not np.std(a) or not np.std(p):
        return None
    return float(np.corrcoef(a, p)[0, 1])


def _spearman(actual: Iterable[float], predicted: Iterable[float]) -> float:
    a, p = np.asarray(list(actual), dtype=float), np.asarray(list(predicted), dtype=float)
    a_rank = pd.Series(a).rank(method="average").to_numpy()
    p_rank = pd.Series(p).rank(method="average").to_numpy()
    return float(np.corrcoef(a_rank, p_rank)[0, 1])


def _calibration_line(y: np.ndarray, p: np.ndarray) -> tuple[float | None, float | None]:
    x = np.log(np.clip(p, 1e-6, 1 - 1e-6) / np.clip(1 - p, 1e-6, 1))
    if len(y) < 2 or np.std(x) == 0:
        return None, None
    slope, intercept = np.polyfit(x, y, 1)
    return float(slope), float(intercept)


def _equal_frequency_indices(values: np.ndarray, bins: int) -> list[np.ndarray]:
    order = np.argsort(values, kind="stable")
    return [chunk for chunk in np.array_split(order, min(bins, len(order)))]
