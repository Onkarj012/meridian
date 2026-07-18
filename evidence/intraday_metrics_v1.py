"""V1 baselines, metrics, uncertainty, and mechanical gate verdicts."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score, balanced_accuracy_score, confusion_matrix, f1_score,
    mean_absolute_error, mean_squared_error, precision_recall_fscore_support,
    r2_score, roc_auc_score,
)

from evidence.intraday_folds_v1 import PROTOCOL_PATH, load_evaluation_protocol


CLASS_ORDER = ("DOWN", "FLAT", "UP")
SEED = 20260718


def direction_metrics(y_true: Iterable[Any], probabilities: pd.DataFrame | np.ndarray) -> dict[str, Any]:
    truth = _labels(y_true)
    probs = _probs(probabilities)
    predicted = np.asarray(CLASS_ORDER)[probs.argmax(axis=1)]
    precision, recall, _, support = precision_recall_fscore_support(
        truth, predicted, labels=CLASS_ORDER, zero_division=0,
    )
    result: dict[str, Any] = {
        "accuracy": _scalar(accuracy_score(truth, predicted)),
        "balanced_accuracy": _scalar(balanced_accuracy_score(truth, predicted)),
        "macro_f1": _scalar(f1_score(truth, predicted, labels=CLASS_ORDER, average="macro", zero_division=0)),
        "multiclass_brier": multiclass_brier(truth, probs),
        "confusion_matrix": confusion_matrix(truth, predicted, labels=CLASS_ORDER).tolist(),
        "per_class": {
            label: {"precision": float(precision[i]), "recall": float(recall[i]), "support": int(support[i])}
            for i, label in enumerate(CLASS_ORDER)
        },
    }
    try:
        result["macro_ovr_auc"] = float(roc_auc_score(truth, probs, labels=list(CLASS_ORDER), multi_class="ovr", average="macro"))
    except ValueError:
        result["macro_ovr_auc"] = None
    non_flat = np.isin(truth, ["DOWN", "UP"])
    if non_flat.any() and len(np.unique(truth[non_flat])) == 2:
        result["up_vs_down_auc"] = float(roc_auc_score(
            (truth[non_flat] == "UP").astype(int),
            probs[non_flat, 2] / np.maximum(probs[non_flat, 0] + probs[non_flat, 2], 1e-12),
        ))
    else:
        result["up_vs_down_auc"] = None
    return result


def multiclass_brier(y_true: Iterable[Any], probabilities: pd.DataFrame | np.ndarray) -> float | None:
    truth = _labels(y_true)
    probs = _probs(probabilities)
    if not len(truth):
        return None
    one_hot = np.column_stack([(truth == label).astype(float) for label in CLASS_ORDER])
    return float(np.mean(np.sum((probs - one_hot) ** 2, axis=1)))


def confidence_metrics(correctness: Iterable[int], confidence: Iterable[float], bins: int = 10) -> dict[str, Any]:
    y = np.asarray(list(correctness), dtype=float)
    p = np.asarray(list(confidence), dtype=float)
    valid = np.isfinite(y) & np.isfinite(p)
    y, p = y[valid], np.clip(p[valid], 0, 1)
    if not len(y):
        return _empty_confidence()
    edges = np.unique(np.quantile(p, np.linspace(0, 1, min(int(bins), len(p)) + 1)))
    bin_ids = np.searchsorted(edges, p, side="right") - 1
    bin_ids = np.clip(bin_ids, 0, max(len(edges) - 2, 0))
    reliability = []
    for index in range(max(len(edges) - 1, 1)):
        selected = bin_ids == index
        if selected.any():
            reliability.append({
                "bin": index, "count": int(selected.sum()),
                "mean_confidence": float(p[selected].mean()),
                "empirical_accuracy": float(y[selected].mean()),
            })
    ece = sum(item["count"] / len(y) * abs(item["mean_confidence"] - item["empirical_accuracy"]) for item in reliability)
    deciles = {str(i + 1): float(y[order].mean()) for i, order in enumerate(_equal_frequency_indices(p, 10)) if len(order)}
    decile_values = np.asarray(list(deciles.values()), dtype=float)
    result = {
        "brier": float(np.mean((p - y) ** 2)),
        "ece": float(ece),
        "reliability_curve": reliability,
        "accuracy_by_confidence_decile": deciles,
        "decile_accuracy_spearman": _spearman(np.arange(1, len(decile_values) + 1), decile_values) if len(decile_values) > 1 else None,
        "top_minus_bottom_decile_accuracy": float(decile_values[-1] - decile_values[0]) if len(decile_values) > 1 else None,
    }
    return result


def magnitude_metrics(
    y_true: Iterable[float],
    prediction: Iterable[float],
    *,
    timestamps: Iterable[Any] | None = None,
    sessions: Iterable[Any] | None = None,
    regimes: Iterable[Any] | None = None,
    missing_source: Iterable[Any] | None = None,
    baseline_predictions: Mapping[str, Iterable[float]] | None = None,
    non_overlapping: tuple[Iterable[float], ...] | None = None,
    protocol_path: str | Path = PROTOCOL_PATH,
    bootstrap_draws: int | None = None,
) -> dict[str, Any]:
    actual = np.asarray(list(y_true), dtype=float)
    predicted = np.asarray(list(prediction), dtype=float)
    valid = np.isfinite(actual) & np.isfinite(predicted)
    actual, predicted = actual[valid], predicted[valid]
    result: dict[str, Any] = {
        "mae_bps": float(mean_absolute_error(actual, predicted)) if len(actual) else None,
        "rmse_bps": float(np.sqrt(mean_squared_error(actual, predicted))) if len(actual) else None,
        "r2": float(r2_score(actual, predicted)) if len(actual) > 1 else None,
        "pearson": _pearson(actual, predicted),
        "relative_mae_skill": None,
        "best_baseline": None,
        "daily_spearman_ic": _empty_ic(),
        "predicted_vs_realized_magnitude_deciles": [],
        "slices": {},
        "bootstrap_skill_ci95": None,
    }
    if timestamps is not None:
        ts_all = pd.to_datetime(pd.Series(list(timestamps)), errors="coerce")
        ts = ts_all.iloc[np.flatnonzero(valid)].reset_index(drop=True)
        result["daily_spearman_ic"] = _daily_ic(actual, predicted, ts)
        result["predicted_vs_realized_magnitude_deciles"] = _magnitude_deciles(actual, predicted)
        result["slices"] = _magnitude_slices(actual, predicted, ts, regimes, missing_source)
    if baseline_predictions:
        baseline_maes = {
            name: float(mean_absolute_error(actual, np.asarray(list(values), dtype=float)[valid]))
            for name, values in baseline_predictions.items()
        }
        if baseline_maes:
            best_name = min(baseline_maes, key=baseline_maes.get)
            result["baseline_mae_bps"] = baseline_maes
            result["best_baseline"] = best_name
            result["relative_mae_skill"] = relative_mae_skill(result["mae_bps"], baseline_maes[best_name])
            if sessions is not None:
                session_values = np.asarray(list(sessions))[valid]
                draws = bootstrap_draws if bootstrap_draws is not None else int(load_evaluation_protocol(protocol_path)["bootstrap"]["draws"])
                result["bootstrap_skill_ci95"] = paired_skill_bootstrap_ci(
                    actual, predicted, np.asarray(list(baseline_predictions[best_name]), dtype=float)[valid],
                    session_values, n_bootstrap=draws,
                )
    if non_overlapping is not None:
        if len(non_overlapping) == 3:
            no_actual, no_model, no_baseline = (np.asarray(list(value), dtype=float) for value in non_overlapping)
            if len(no_actual) and len(no_model) == len(no_actual) and len(no_baseline) == len(no_actual):
                result["non_overlapping_mae_bps"] = float(mean_absolute_error(no_actual, no_model))
                result["non_overlapping_mae_skill"] = relative_mae_skill(result["non_overlapping_mae_bps"], float(mean_absolute_error(no_actual, no_baseline)))
            else:
                result["non_overlapping_mae_skill"] = None
        else:
            result["non_overlapping_mae_skill"] = None
    return result


def relative_mae_skill(model_mae: float | Iterable[float], best_baseline_mae: float | Iterable[float]) -> float | None:
    """Compute the registered relative MAE skill, with no outcome selection."""
    model = float(np.asarray(model_mae, dtype=float).mean())
    baseline = float(np.asarray(best_baseline_mae, dtype=float).mean())
    if not np.isfinite(model) or not np.isfinite(baseline) or baseline <= 0:
        return None
    return float(1.0 - model / baseline)


def fixed_magnitude_baselines(
    train_rows: pd.DataFrame,
    evaluation_rows: pd.DataFrame,
    horizon: int,
    *,
    scale_column: str | None = None,
) -> dict[str, np.ndarray]:
    """Return the four fixed magnitude baselines using training rows only."""
    abs_column = _first_column(train_rows, (f"target_h{horizon}_return_bps", f"target_h{horizon}_mag_bps"))
    normalized_column = _first_column(train_rows, (f"target_h{horizon}_normalized_magnitude",))
    scale_column = scale_column or f"target_h{horizon}_scale"
    train_abs = pd.to_numeric(train_rows[abs_column], errors="coerce").abs()
    train_u = pd.to_numeric(train_rows[normalized_column], errors="coerce")
    global_abs = float(train_abs.median())
    global_u = float(train_u.median())
    if not np.isfinite(global_abs):
        global_abs = 0.0
    if not np.isfinite(global_u):
        global_u = 0.0
    scale = pd.to_numeric(evaluation_rows.get(scale_column, 1.0), errors="coerce").fillna(1.0).to_numpy(dtype=float)
    zero = np.zeros(len(evaluation_rows), dtype=float)
    unconditional = np.full(len(evaluation_rows), global_abs, dtype=float)
    scale_only = global_u * scale
    train_bucket = _time_bucket(train_rows)
    eval_bucket = _time_bucket(evaluation_rows)
    bucket_medians = pd.DataFrame({"bucket": train_bucket, "u": train_u}).groupby("bucket")["u"].median()
    bucket_u = eval_bucket.map(bucket_medians).fillna(global_u).to_numpy(dtype=float)
    return {
        "zero_magnitude": zero,
        "unconditional_train_median_abs": unconditional,
        "scale_only_train_median": scale_only,
        "time_bucket_30m_train_median": bucket_u * scale,
    }


magnitude_baselines = fixed_magnitude_baselines


def direction_baselines(
    train_rows: pd.DataFrame,
    evaluation_rows: pd.DataFrame,
    horizon: int,
    *,
    feature_columns: Iterable[str] | None = None,
) -> dict[str, pd.DataFrame]:
    """Return fixed direction-baseline probability frames."""
    target = f"target_h{horizon}_dir"
    labels = pd.Categorical(train_rows[target], categories=CLASS_ORDER).dropna()
    majority = str(pd.Series(labels).value_counts().reindex(CLASS_ORDER).fillna(0).idxmax())
    result = {
        "random_1_3": _constant_probabilities(len(evaluation_rows), 1 / 3),
        "always_majority": _label_probabilities(np.full(len(evaluation_rows), majority), 1.0),
        "last_5m_sign": _last_five_minute_probabilities(evaluation_rows),
    }
    if feature_columns is not None:
        columns = list(feature_columns)
        linear = _fit_linear_direction(train_rows, evaluation_rows, target, columns)
        result["multinomial_linear"] = linear
        try:
            from models.intraday_predictor import fit_horizon_models, predict_horizon
            target_frame = train_rows.copy()
            target_frame[f"target_h{horizon}_mag_bps"] = target_frame.get(f"target_h{horizon}_return_bps", 0.0)
            bundle = fit_horizon_models(train_rows[columns], target_frame, np.ones(len(train_rows), dtype=bool), horizon)
            result["frozen_v0_lightgbm"] = predict_horizon(bundle, evaluation_rows[columns])
        except (ImportError, ValueError, TypeError):
            result["frozen_v0_lightgbm"] = _constant_probabilities(len(evaluation_rows), 1 / 3)
    return result


def non_overlapping_decisions(frame: pd.DataFrame, horizon: int) -> pd.DataFrame:
    if "datetime" not in frame:
        raise ValueError("frame requires datetime")
    result = frame.copy()
    timestamps = pd.to_datetime(result["datetime"], errors="raise")
    minutes = timestamps.dt.hour * 60 + timestamps.dt.minute
    start = timestamps.dt.normalize() + pd.Timedelta(minutes=570)
    offsets = (timestamps - start).dt.total_seconds() / 60
    return result.loc[(minutes >= 570) & (offsets >= 0) & (offsets % int(horizon) == 0)].copy()


def paired_session_block_bootstrap(
    candidate: Iterable[float], baseline: Iterable[float], sessions: Iterable[Any], *,
    n_bootstrap: int | None = None, seed: int = SEED, confidence: float = 0.95,
    protocol_path: str | Path = PROTOCOL_PATH,
) -> tuple[float, float]:
    candidate = np.asarray(list(candidate), dtype=float)
    baseline = np.asarray(list(baseline), dtype=float)
    sessions = np.asarray(list(sessions))
    if n_bootstrap is None:
        n_bootstrap = int(load_evaluation_protocol(protocol_path)["bootstrap"]["draws"])
    if not (len(candidate) == len(baseline) == len(sessions)) or not len(candidate):
        raise ValueError("paired values and sessions must align and be non-empty")
    unique = pd.unique(sessions)
    groups = [np.flatnonzero(sessions == value) for value in unique]
    rng = np.random.default_rng(seed)
    draws = np.empty(int(n_bootstrap), dtype=float)
    for i in range(len(draws)):
        sampled = rng.integers(0, len(groups), size=len(groups))
        indices = np.concatenate([groups[index] for index in sampled])
        draws[i] = float(np.mean(candidate[indices] - baseline[indices]))
    alpha = (1 - confidence) / 2
    return float(np.quantile(draws, alpha)), float(np.quantile(draws, 1 - alpha))


def paired_skill_bootstrap_ci(actual: np.ndarray, candidate: np.ndarray, baseline: np.ndarray, sessions: np.ndarray, *, n_bootstrap: int = 2_000, seed: int = SEED) -> tuple[float, float]:
    unique = pd.unique(sessions)
    groups = [np.flatnonzero(sessions == value) for value in unique]
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(int(n_bootstrap)):
        sampled = rng.integers(0, len(groups), size=len(groups))
        indices = np.concatenate([groups[index] for index in sampled])
        base_mae = float(np.mean(np.abs(actual[indices] - baseline[indices])))
        model_mae = float(np.mean(np.abs(actual[indices] - candidate[indices])))
        draws.append(relative_mae_skill(model_mae, base_mae))
    alpha = (1 - 0.95) / 2
    return float(np.quantile(draws, alpha)), float(np.quantile(draws, 1 - alpha))


def development_gate_verdicts(metrics: Mapping[str, Any], *, protocol: Mapping[str, Any] | None = None, horizon: int | str | None = None) -> dict[str, Any]:
    """Evaluate registered gates mechanically and return structured verdicts."""
    protocol = protocol or load_evaluation_protocol()
    gates = protocol["development_gates"]
    hkey = f"H{horizon}" if horizon is not None and not str(horizon).upper().startswith("H") else str(horizon).upper() if horizon is not None else None
    checks: dict[str, dict[str, Any]] = {}
    direction = metrics.get("direction", metrics)
    checks["direction_balanced_accuracy"] = _check("gte", direction.get("pooled_balanced_accuracy", direction.get("balanced_accuracy")), gates["direction"]["pooled_balanced_accuracy_min"])
    checks["direction_macro_auc"] = _check("gte", direction.get("pooled_macro_auc", direction.get("macro_ovr_auc")), gates["direction"]["macro_auc_min"])
    fold_bas = direction.get("fold_balanced_accuracies", direction.get("folds_above_random", []))
    if isinstance(fold_bas, list) and fold_bas and isinstance(fold_bas[0], (int, float)):
        value = sum(float(v) > 1 / 3 for v in fold_bas)
    else:
        value = direction.get("folds_with_balanced_accuracy_above_random")
    checks["direction_positive_folds"] = _check("gte", value, gates["direction"]["folds_with_balanced_accuracy_above_random_min"])
    confidence = metrics.get("confidence", {})
    if hkey in gates["confidence"]:
        conf = confidence
        threshold = gates["confidence"][hkey]
        checks["confidence_ece"] = _check("lte", conf.get("ece"), threshold["ece_max"])
        checks["confidence_decile_spearman"] = _check("gte", conf.get("decile_accuracy_spearman"), threshold["decile_accuracy_spearman_min"])
        checks["confidence_top_minus_bottom"] = _check("gte", conf.get("top_minus_bottom_decile_accuracy"), threshold["top_minus_bottom_decile_accuracy_min"])
    magnitude = metrics.get("magnitude", {})
    mag_gate = gates["magnitude"]
    checks["magnitude_relative_skill"] = _check("gte", magnitude.get("relative_mae_skill"), mag_gate["relative_mae_skill_min"])
    checks["magnitude_positive_folds"] = _check("gte", magnitude.get("positive_skill_folds"), mag_gate["folds_with_positive_skill_min"])
    checks["magnitude_daily_ic_mean"] = _check("gte", _nested(magnitude, "daily_spearman_ic", "mean"), mag_gate["daily_ic_mean_min"])
    checks["magnitude_positive_day_fraction"] = _check("gte", _nested(magnitude, "daily_spearman_ic", "positive_fraction"), mag_gate["positive_day_ic_fraction_min"])
    checks["magnitude_non_overlapping_skill"] = _check("gte", magnitude.get("non_overlapping_mae_skill"), mag_gate["non_overlapping_mae_skill_min"])
    return {"status": "passed" if all(item["passed"] for item in checks.values()) else "failed", "passed": all(item["passed"] for item in checks.values()), "checks": checks, "horizon": hkey}


gate_verdicts = development_gate_verdicts


def _check(operator: str, value: Any, threshold: float) -> dict[str, Any]:
    passed = False if value is None else value >= threshold if operator == "gte" else value <= threshold
    return {"passed": bool(passed), "operator": operator, "value": None if value is None else float(value), "threshold": float(threshold)}


def _nested(value: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _labels(value: Iterable[Any]) -> np.ndarray:
    return np.asarray(pd.Categorical(np.asarray(list(value)), categories=CLASS_ORDER).astype(object))


def _probs(value: pd.DataFrame | np.ndarray) -> np.ndarray:
    if isinstance(value, pd.DataFrame) and {"p_down_raw", "p_flat_raw", "p_up_raw"}.issubset(value.columns):
        value = value[["p_down_raw", "p_flat_raw", "p_up_raw"]]
    array = np.asarray(value, dtype=float)
    if array.ndim != 2 or array.shape[1] != 3:
        raise ValueError("probabilities must have three columns")
    return array


def _constant_probabilities(length: int, value: float) -> pd.DataFrame:
    return pd.DataFrame({"p_down_raw": np.full(length, value), "p_flat_raw": np.full(length, value), "p_up_raw": np.full(length, value), "direction": np.full(length, "DOWN")})


def _label_probabilities(labels: np.ndarray, confidence: float) -> pd.DataFrame:
    result = pd.DataFrame({"p_down_raw": (labels == "DOWN").astype(float) * confidence, "p_flat_raw": (labels == "FLAT").astype(float) * confidence, "p_up_raw": (labels == "UP").astype(float) * confidence, "direction": labels})
    return result


def _last_five_minute_probabilities(frame: pd.DataFrame) -> pd.DataFrame:
    source = frame.get("log_ret_5m", frame.get("last_5m_return", pd.Series(0.0, index=frame.index)))
    labels = np.where(pd.to_numeric(source, errors="coerce").fillna(0).to_numpy() >= 0, "UP", "DOWN")
    return _label_probabilities(labels, 1.0)


def _fit_linear_direction(train: pd.DataFrame, evaluation: pd.DataFrame, target: str, columns: list[str]) -> pd.DataFrame:
    x_train, x_eval = _numeric_features(train[columns]), _numeric_features(evaluation[columns])
    x_eval = x_eval.reindex(columns=x_train.columns, fill_value=0.0)
    y = pd.Categorical(train[target], categories=CLASS_ORDER).codes
    if len(np.unique(y[y >= 0])) < 2:
        return _label_probabilities(np.full(len(evaluation), CLASS_ORDER[int(y[y >= 0][0])] if (y >= 0).any() else "FLAT"), 1.0)
    try:
        model = LogisticRegression(max_iter=1000, multi_class="multinomial", random_state=SEED)
    except TypeError:
        model = LogisticRegression(max_iter=1000, random_state=SEED)
    model.fit(x_train, y)
    raw = model.predict_proba(x_eval)
    result = np.zeros((len(evaluation), 3), dtype=float)
    for index, label in enumerate(model.classes_):
        result[:, int(label)] = raw[:, index]
    return pd.DataFrame({"p_down_raw": result[:, 0], "p_flat_raw": result[:, 1], "p_up_raw": result[:, 2], "direction": np.asarray(CLASS_ORDER)[result.argmax(axis=1)]})


def _numeric_features(frame: pd.DataFrame) -> pd.DataFrame:
    result = pd.get_dummies(frame, dummy_na=True)
    result = result.apply(pd.to_numeric, errors="coerce")
    return result.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def _first_column(frame: pd.DataFrame, candidates: tuple[str, ...]) -> str:
    for name in candidates:
        if name in frame:
            return name
    raise ValueError(f"rows need one of {candidates}")


def _time_bucket(frame: pd.DataFrame) -> pd.Series:
    timestamps = pd.to_datetime(frame["datetime"], errors="raise")
    return ((timestamps.dt.hour * 60 + timestamps.dt.minute) // 30).astype(int)


def _pearson(actual: np.ndarray, predicted: np.ndarray) -> float | None:
    if len(actual) < 2 or not np.std(actual) or not np.std(predicted):
        return None
    return float(np.corrcoef(actual, predicted)[0, 1])


def _spearman(actual: Iterable[float], predicted: Iterable[float]) -> float | None:
    a, p = np.asarray(list(actual), dtype=float), np.asarray(list(predicted), dtype=float)
    if len(a) < 2 or not np.std(a) or not np.std(p):
        return None
    return float(np.corrcoef(pd.Series(a).rank().to_numpy(), pd.Series(p).rank().to_numpy())[0, 1])


def _daily_ic(actual: np.ndarray, predicted: np.ndarray, timestamps: pd.Series) -> dict[str, Any]:
    values = {}
    for day, group in pd.DataFrame({"actual": actual, "predicted": predicted, "day": timestamps.dt.normalize()}).groupby("day"):
        ic = _spearman(group["actual"], group["predicted"])
        if ic is not None:
            values[str(day.date())] = ic
    series = np.asarray(list(values.values()), dtype=float)
    return {"count": int(len(series)), "mean": float(series.mean()) if len(series) else None, "median": float(np.median(series)) if len(series) else None, "positive_fraction": float((series > 0).mean()) if len(series) else None, "values": values}


def _magnitude_deciles(actual: np.ndarray, predicted: np.ndarray) -> list[dict[str, Any]]:
    rows = []
    for index in _equal_frequency_indices(predicted, 10):
        if len(index):
            rows.append({"decile": len(rows) + 1, "predicted_median": float(np.median(predicted[index])), "realized_median": float(np.median(actual[index])), "count": int(len(index))})
    rows_monotonic = all(rows[i]["predicted_median"] <= rows[i + 1]["predicted_median"] for i in range(len(rows) - 1))
    for row in rows:
        row["monotonic_predicted"] = rows_monotonic
    return rows


def _magnitude_slices(actual: np.ndarray, predicted: np.ndarray, timestamps: pd.Series, regimes: Iterable[Any] | None, missing_source: Iterable[Any] | None) -> dict[str, Any]:
    frame = pd.DataFrame({"actual": actual, "predicted": predicted, "timestamp": timestamps.reset_index(drop=True)})
    frame["quarter"] = frame["timestamp"].dt.to_period("Q").astype(str)
    minutes = frame["timestamp"].dt.hour * 60 + frame["timestamp"].dt.minute
    frame["time_of_day"] = pd.cut(minutes, [569, 690, 810, 930], labels=["open", "midday", "close"])
    frame["regime"] = list(regimes)[:len(frame)] if regimes is not None else "unknown"
    frame["missing_source"] = list(missing_source)[:len(frame)] if missing_source is not None else "unknown"
    result = {}
    for column in ("quarter", "time_of_day", "regime", "missing_source"):
        result[column] = {str(key): _slice_metrics(group["actual"], group["predicted"]) for key, group in frame.groupby(column, dropna=False)}
    return result


def _slice_metrics(actual: Iterable[float], predicted: Iterable[float]) -> dict[str, Any]:
    a, p = np.asarray(list(actual), dtype=float), np.asarray(list(predicted), dtype=float)
    return {"rows": int(len(a)), "mae_bps": float(mean_absolute_error(a, p)) if len(a) else None, "pearson": _pearson(a, p), "spearman": _spearman(a, p)}


def _equal_frequency_indices(values: np.ndarray, bins: int) -> list[np.ndarray]:
    order = np.argsort(values, kind="stable")
    return [chunk for chunk in np.array_split(order, min(int(bins), len(order)))] if len(order) else []


def _empty_ic() -> dict[str, Any]:
    return {"count": 0, "mean": None, "median": None, "positive_fraction": None, "values": {}}


def _empty_confidence() -> dict[str, Any]:
    return {"brier": None, "ece": None, "reliability_curve": [], "accuracy_by_confidence_decile": {}, "decile_accuracy_spearman": None, "top_minus_bottom_decile_accuracy": None}


def _scalar(value: Any) -> float:
    return float(value)
