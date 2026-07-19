"""Prospective v1.1-smooth metrics, bootstrap uncertainty, and gates."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np
import pandas as pd

from evidence.intraday_metrics_v1 import confidence_metrics, direction_metrics
from models.intraday_confidence import correctness_labels


SEED = 20260718
BOOTSTRAP_DRAWS = 2_000
CLASS_ORDER = ("DOWN", "FLAT", "UP")


def evaluate_predictions(
    predictions: pd.DataFrame,
    horizon: int,
    *,
    s0_predictions: pd.DataFrame | None = None,
    threshold: float | None = None,
    cost_spec: Mapping[str, Any] | None = None,
    bootstrap_draws: int = BOOTSTRAP_DRAWS,
    seed: int = SEED,
) -> dict[str, Any]:
    """Compute registered metrics and a mechanical gate table for one horizon."""
    frame = predictions.copy().reset_index(drop=True)
    smooth_truth = _series(frame, horizon, "smooth_dir", "realized_smooth_dir", required=False)
    raw_truth = _series(frame, horizon, "raw_dir", "realized_dir", f"target_h{horizon}_dir", required=False)
    probabilities = _probabilities(frame)
    result: dict[str, Any] = {"horizon": int(horizon), "rows": int(len(frame))}
    if smooth_truth is not None:
        result["smoothed_direction"] = direction_metrics(smooth_truth, probabilities)
        result["smoothed_class_share"] = _class_shares(smooth_truth)
    else:
        result["smoothed_direction"] = _not_evaluable("smoothed truth missing")
        result["smoothed_class_share"] = {"valid": False, "structural_failure": True}
    if raw_truth is not None:
        result["raw_direction"] = direction_metrics(raw_truth, probabilities)
        result["raw_direction_halves"] = _raw_half_metrics(frame, raw_truth, probabilities)
    else:
        result["raw_direction"] = _not_evaluable("raw truth missing")

    if s0_predictions is not None and raw_truth is not None:
        s0_probs = _probabilities(s0_predictions.reset_index(drop=True))
        s0_metrics = direction_metrics(raw_truth, s0_probs)
        result["s3_vs_s0_raw_delta_ba"] = float(result["raw_direction"]["balanced_accuracy"] - s0_metrics["balanced_accuracy"])
        result["s0_raw_direction"] = s0_metrics
    else:
        result["s3_vs_s0_raw_delta_ba"] = None
        result["s0_raw_direction"] = _not_evaluable("S0 comparator missing")

    if raw_truth is not None:
        correctness = correctness_labels(probabilities, raw_truth)
        confidence = _confidence(frame, probabilities)
        result["confidence"] = confidence_metrics(correctness, confidence)
    else:
        correctness = np.zeros(len(frame), dtype=int)
        confidence = _confidence(frame, probabilities)
        result["confidence"] = _not_evaluable("raw truth missing")

    primary = threshold if threshold is not None else _threshold_column(frame)
    if primary is None:
        result["confidence_filter"] = _not_evaluable("primary threshold missing")
    else:
        result["confidence_filter"] = _filter_metrics(frame, raw_truth, confidence, float(primary), int(horizon), bootstrap_draws, seed)
    result["economic"] = _economic_metrics(frame, cost_spec, int(horizon), bootstrap_draws, seed)
    result["gates"] = evaluate_gates(result, int(horizon))
    return result


def evaluate_gates(metrics: Mapping[str, Any], horizon: int) -> dict[str, Any]:
    """Return PASS/FAIL/NOT_EVALUABLE for every frozen registration gate."""
    h = int(horizon)
    confidence_limit = 0.030 if h == 15 else 0.050
    smooth = metrics.get("smoothed_direction", {})
    raw = metrics.get("raw_direction", {})
    conf = metrics.get("confidence", {})
    filt = metrics.get("confidence_filter", {})
    economic = metrics.get("economic", {})
    checks = {
        "smoothed_label_balanced_accuracy": _gate(smooth.get("balanced_accuracy"), 0.400, "gte"),
        "smoothed_label_macro_auc": _gate(smooth.get("macro_ovr_auc"), 0.560, "gte"),
        "raw_endpoint_balanced_accuracy": _gate(raw.get("balanced_accuracy"), 0.370, "gte"),
        "raw_endpoint_macro_auc": _gate(raw.get("macro_ovr_auc"), 0.540, "gte"),
        "raw_endpoint_ba_improvement_over_s0": _gate(metrics.get("s3_vs_s0_raw_delta_ba"), 0.003, "gte"),
        "raw_direction_first_half": _half_gate(metrics, h, 0, 0.370),
        "raw_direction_second_half": _half_gate(metrics, h, 1, 0.370),
        "class_share_validity": _gate((metrics.get("smoothed_class_share") or {}).get("valid"), True, "eq"),
        "confidence_ece": _gate(conf.get("ece"), confidence_limit, "lte"),
        "confidence_decile_accuracy_spearman": _gate(conf.get("decile_accuracy_spearman"), 0.80, "gte"),
        "confidence_top_minus_bottom_decile_accuracy": _gate(conf.get("top_minus_bottom_decile_accuracy"), 0.08, "gte"),
        "filtered_actionable_accuracy_improvement": _gate(filt.get("accuracy_improvement"), 0.030, "gte"),
        "filtered_bootstrap_lower_bound": _gate(filt.get("bootstrap_ci95", {}).get("lower"), 0.0, "gt"),
        "realized_coverage": _gate(filt.get("realized_coverage"), (0.15, 0.35), "between"),
        "non_overlapping_accepted": _gate(filt.get("non_overlapping_accepted"), 500 if h == 15 else 150, "gte"),
        "net_mean_return_after_costs": _economic_gate(economic, "net_mean_return", 0.0, "gt"),
        "net_return_bootstrap_lower_bound": _economic_gate(economic, "bootstrap_ci95_lower", 0.0, "gt"),
        "net_annualized_sharpe": _economic_gate(economic, "annualized_sharpe", 0.50, "gte"),
    }
    passed = all(item["status"] == "PASS" for item in checks.values())
    return {"status": "PASS" if passed else "FAIL", "passed": passed, "checks": checks, "horizon": f"H{h}"}


mechanical_gate_table = evaluate_gates
gate_table = evaluate_gates


def paired_filter_bootstrap_ci(
    predictions: pd.DataFrame,
    threshold: float,
    *,
    horizon: int,
    draws: int = BOOTSTRAP_DRAWS,
    seed: int = SEED,
) -> dict[str, float | int]:
    """Bootstrap filtered-vs-unfiltered actionable accuracy by session block."""
    frame = predictions.reset_index(drop=True)
    probabilities = _probabilities(frame)
    truth = _series(frame, horizon, "raw_dir", "realized_dir", required=True).astype(str).to_numpy()
    predicted = np.asarray(CLASS_ORDER)[probabilities.argmax(axis=1)]
    confidence = _confidence(frame, probabilities)
    sessions = _sessions(frame)
    actionable = np.isin(predicted, ("UP", "DOWN"))
    accepted = actionable & (confidence >= float(threshold))
    unique = pd.unique(sessions)
    groups = [np.flatnonzero(sessions == value) for value in unique]
    rng = np.random.default_rng(seed)
    samples = np.empty(int(draws), dtype=float)
    for draw in range(len(samples)):
        selected = np.concatenate([groups[index] for index in rng.integers(0, len(groups), size=len(groups))])
        samples[draw] = _accuracy_lift(truth[selected], predicted[selected], accepted[selected], actionable[selected])
    estimate = _accuracy_lift(truth, predicted, accepted, actionable)
    return {
        "estimate": float(estimate),
        "lower": float(np.quantile(samples, 0.025)),
        "upper": float(np.quantile(samples, 0.975)),
        "draws": int(draws),
        "seed": int(seed),
    }


def paired_bootstrap_improvement_ci(*args: Any, **kwargs: Any) -> tuple[float, float]:
    """Tuple-returning compatibility helper for the registered paired CI."""
    ci = paired_filter_bootstrap_ci(*args, **kwargs)
    return float(ci["lower"]), float(ci["upper"])


def _filter_metrics(
    frame: pd.DataFrame,
    truth: pd.Series | None,
    confidence: np.ndarray,
    threshold: float,
    horizon: int,
    draws: int,
    seed: int,
) -> dict[str, Any]:
    if truth is None:
        return _not_evaluable("raw truth missing")
    probabilities = _probabilities(frame)
    predicted = np.asarray(CLASS_ORDER)[probabilities.argmax(axis=1)]
    truth_values = truth.astype(str).to_numpy()
    actionable = np.isin(predicted, ("UP", "DOWN"))
    accepted = actionable & (confidence >= threshold)
    unfiltered = float(np.mean(predicted[actionable] == truth_values[actionable])) if actionable.any() else None
    filtered = float(np.mean(predicted[accepted] == truth_values[accepted])) if accepted.any() else None
    ci = paired_filter_bootstrap_ci(frame, threshold, horizon=horizon, draws=draws, seed=seed) if actionable.any() else {"estimate": None, "lower": None, "upper": None, "draws": draws, "seed": seed}
    non_overlap = _non_overlapping_count(frame.loc[accepted], horizon)
    return {
        "threshold": threshold,
        "actionable_rows": int(actionable.sum()),
        "accepted_rows": int(accepted.sum()),
        "unfiltered_actionable_accuracy": unfiltered,
        "filtered_actionable_accuracy": filtered,
        "accuracy_improvement": None if filtered is None or unfiltered is None else filtered - unfiltered,
        "bootstrap_ci95": ci,
        "realized_coverage": float(accepted.sum() / len(frame)) if len(frame) else None,
        "non_overlapping_accepted": int(non_overlap),
    }


def _economic_metrics(frame: pd.DataFrame, cost_spec: Mapping[str, Any] | None, horizon: int, draws: int, seed: int) -> dict[str, Any]:
    if cost_spec is None:
        return {"status": "NOT_EVALUABLE", "reason": "frozen cost spec artifact was not supplied"}
    return compute_cost_metrics(frame, cost_spec, horizon=horizon, draws=draws, seed=seed)


def compute_cost_metrics(frame: pd.DataFrame, cost_spec: Mapping[str, Any], *, horizon: int, draws: int, seed: int) -> dict[str, Any]:
    """Compute economic legs only from an explicitly supplied frozen cost spec."""
    return_column = str(cost_spec.get("net_return_column", "net_return_bps"))
    if return_column not in frame:
        return {"status": "NOT_EVALUABLE", "reason": f"cost spec return column {return_column!r} is missing"}
    values = pd.to_numeric(frame[return_column], errors="coerce").dropna().to_numpy(dtype=float)
    if not len(values):
        return {"status": "NOT_EVALUABLE", "reason": "no finite net returns"}
    sessions = _sessions(frame.loc[frame[return_column].notna()])
    unique = pd.unique(sessions)
    rng = np.random.default_rng(seed)
    groups = [np.flatnonzero(sessions == value) for value in unique]
    means = np.empty(int(draws), dtype=float)
    for draw in range(len(means)):
        selected = np.concatenate([groups[index] for index in rng.integers(0, len(groups), size=len(groups))])
        means[draw] = float(values[selected].mean())
    annualized = float(values.mean() / values.std(ddof=1) * np.sqrt(252)) if len(values) > 1 and values.std(ddof=1) else None
    return {
        "status": "ok",
        "net_mean_return": float(values.mean()),
        "bootstrap_ci95_lower": float(np.quantile(means, 0.025)),
        "bootstrap_ci95_upper": float(np.quantile(means, 0.975)),
        "annualized_sharpe": annualized,
        "cost_spec": dict(cost_spec),
    }


def _half_gate(metrics: Mapping[str, Any], horizon: int, half: int, threshold: float) -> dict[str, Any]:
    halves = metrics.get("raw_direction_halves")
    if halves is None:
        return _not_evaluable("half-period raw metrics missing")
    values = halves if isinstance(halves, list) else [halves.get("first"), halves.get("second")]
    if half >= len(values) or not isinstance(values[half], Mapping):
        return _not_evaluable("half-period raw metrics missing")
    return _gate(values[half].get("balanced_accuracy"), threshold, "gte")


def _raw_half_metrics(frame: pd.DataFrame, truth: pd.Series, probabilities: np.ndarray) -> list[dict[str, Any]]:
    if "datetime" in frame:
        order = pd.to_datetime(frame["datetime"], errors="raise").sort_values().index.to_numpy()
    else:
        order = np.arange(len(frame))
    midpoint = len(order) // 2
    return [
        direction_metrics(truth.iloc[order[:midpoint]], probabilities[order[:midpoint]]) if midpoint else {},
        direction_metrics(truth.iloc[order[midpoint:]], probabilities[order[midpoint:]]) if len(order) - midpoint else {},
    ]


def _gate(value: Any, threshold: Any, operator: str) -> dict[str, Any]:
    if value is None:
        return _not_evaluable("metric missing")
    if operator == "gte": passed = float(value) >= float(threshold)
    elif operator == "lte": passed = float(value) <= float(threshold)
    elif operator == "gt": passed = float(value) > float(threshold)
    elif operator == "eq": passed = bool(value) is bool(threshold)
    elif operator == "between": passed = float(threshold[0]) <= float(value) <= float(threshold[1])
    else: raise ValueError(f"unknown gate operator {operator}")
    return {"status": "PASS" if passed else "FAIL", "value": value, "threshold": threshold, "operator": operator}


def _economic_gate(economic: Mapping[str, Any], key: str, threshold: float, operator: str) -> dict[str, Any]:
    if economic.get("status") == "NOT_EVALUABLE":
        return _not_evaluable(economic.get("reason", "cost spec missing"))
    return _gate(economic.get(key), threshold, operator)


def _not_evaluable(reason: str) -> dict[str, Any]:
    return {"status": "NOT_EVALUABLE", "value": None, "reason": reason}


def _series(frame: pd.DataFrame, horizon: int, *names: str, required: bool = False) -> pd.Series | None:
    candidates = list(names) + [f"target_h{horizon}_{name}" for name in names]
    for name in candidates:
        if name in frame:
            return frame[name]
    if required:
        raise ValueError(f"predictions need one of {names}")
    return None


def _probabilities(frame: pd.DataFrame) -> np.ndarray:
    columns = ["p_down_raw", "p_flat_raw", "p_up_raw"]
    if set(columns).issubset(frame.columns):
        return frame[columns].to_numpy(dtype=float)
    if "direction" in frame:
        labels = frame["direction"].astype(str).to_numpy()
        return np.column_stack([(labels == label).astype(float) for label in CLASS_ORDER])
    raise ValueError("predictions need class probability columns or direction")


def _confidence(frame: pd.DataFrame, probabilities: np.ndarray) -> np.ndarray:
    for name in ("confidence", "confidence_correct", "calibrated_confidence"):
        if name in frame:
            return pd.to_numeric(frame[name], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    return probabilities.max(axis=1)


def _threshold_column(frame: pd.DataFrame) -> float | None:
    for name in ("threshold_primary", "confidence_threshold"):
        if name in frame and frame[name].notna().any():
            return float(frame[name].dropna().iloc[0])
    return None


def _class_shares(labels: Iterable[Any]) -> dict[str, Any]:
    values = pd.Series(list(labels), dtype="string").dropna()
    counts = {label: int((values == label).sum()) for label in CLASS_ORDER}
    shares = {label: counts[label] / len(values) if len(values) else 0.0 for label in CLASS_ORDER}
    return {"valid": bool(len(values) and min(shares.values()) >= 0.15), "counts": counts, "shares": shares}


def _sessions(frame: pd.DataFrame) -> np.ndarray:
    if "trade_date" in frame:
        return pd.to_datetime(frame["trade_date"], errors="raise").dt.normalize().astype(str).to_numpy()
    if "datetime" in frame:
        return pd.to_datetime(frame["datetime"], errors="raise").dt.normalize().astype(str).to_numpy()
    return np.arange(len(frame)).astype(str)


def _accuracy_lift(truth: np.ndarray, predicted: np.ndarray, accepted: np.ndarray, actionable: np.ndarray) -> float:
    if not actionable.any() or not accepted.any():
        return 0.0
    return float(np.mean(predicted[accepted] == truth[accepted]) - np.mean(predicted[actionable] == truth[actionable]))


def _non_overlapping_count(frame: pd.DataFrame, horizon: int) -> int:
    if frame.empty:
        return 0
    if "datetime" not in frame:
        return len(frame)
    ordered = frame.assign(_ts=pd.to_datetime(frame["datetime"], errors="raise")).sort_values("_ts")
    count = 0
    last: pd.Timestamp | None = None
    for timestamp in ordered["_ts"]:
        if last is None or timestamp >= last + pd.Timedelta(minutes=horizon):
            count += 1
            last = timestamp
    return count
