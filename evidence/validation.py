"""Validation-only challenger selection and one-shot sealed test evaluation.

This module deliberately makes the 2025 boundary an API boundary: candidate
selection never receives test rows, and ``evaluate_sealed_test`` is the only
function that may read them after a selection artifact has been sealed.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import polars as pl
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression

from evidence.costs import DEFAULT_DELIVERY_ROUND_TRIP_BPS
from evidence.execution_cost import execution_quality_scenarios
from evidence.gates import DEFAULT_PROMOTION_THRESHOLDS
from evidence.stats import economic_metrics


STUDY_FORMAT = "meridian.validation-only-study.v1"
TRAIN_START, TRAIN_END = "2020-01-01", "2022-12-31"
VALIDATION_START, VALIDATION_END, TEST_START, TEST_END = "2023-01-01", "2024-12-31", "2025-01-01", "2025-12-31"
DEFAULT_THRESHOLDS = tuple(round(0.50 + i * 0.02, 2) for i in range(18)) + (0.85,)
# Raw ``atr`` (absolute price units) is intentionally excluded: it let the model
# rank a sub-rupee penny name above large caps purely by price scale. ``atr_bps``
# and the volatility-normalized return carry the same information comparably.
FEATURE_COLUMNS = (
    "return_1", "return_3", "return_6", "return_12", "return_24", "return_48",
    "return_1_vol_norm", "vwap_distance", "volume_ratio", "volume_shock", "atr_bps",
    "rolling_volatility", "intraday_range_position", "opening_gap",
    "open_to_now_return", "relative_strength",
    "sector_return_1", "stock_minus_sector_return",
    "peer_return_1", "peer_correlation", "peer_confirmation", "peer_edge_count",
)
BLOCKED_FEATURE_COLUMN_TOKENS = (
    "news", "headline", "sentiment", "gdelt", "event", "announcement",
    "published_at", "first_seen_at", "available_at",
)
FAMILY_REQUIREMENTS = {
    "random": (),
    "momentum": ("return_1", "return_3"),
    "vwap_relative_strength": ("return_3", "vwap_distance", "relative_strength"),
    "sector_hist_gradient_boosting": ("stock_minus_sector_return", "sector_return_1"),
    "graph_hist_gradient_boosting": ("peer_return_1", "peer_correlation", "peer_confirmation"),
    "side_hist_gradient_boosting": ("return_1", "volume", "atr_bps"),
}
ROBUST_MIN_VALIDATION_TRADES = DEFAULT_PROMOTION_THRESHOLDS.min_trades
ROBUST_MIN_VALIDATION_DAYS = DEFAULT_PROMOTION_THRESHOLDS.min_trading_days
ROBUST_MAX_SYMBOL_TRADE_SHARE = DEFAULT_PROMOTION_THRESHOLDS.max_single_symbol_share
ROBUST_MIN_DISTINCT_SYMBOLS = DEFAULT_PROMOTION_THRESHOLDS.min_distinct_symbols
ROBUST_REGIME_ROW_THRESHOLD = 100_000
SHORT_EXECUTION_CAVEAT = "short_borrow_slb_futures_cost_unmodeled"
FULL_COST_BPS = DEFAULT_DELIVERY_ROUND_TRIP_BPS
COST_SCENARIOS = {"full_cost": FULL_COST_BPS}


@dataclass(frozen=True)
class TemporalFold:
    train_indices: tuple[int, ...]
    validation_indices: tuple[int, ...]
    embargo_until: str


def temporal_split(rows: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Split fixed study periods; 2026 and all other dates are rejected."""
    result = {"train": [], "validation": [], "test": []}
    for row in rows:
        day = _day(row)
        if TRAIN_START <= day <= TRAIN_END:
            result["train"].append(row)
        elif VALIDATION_START <= day <= VALIDATION_END:
            result["validation"].append(row)
        elif TEST_START <= day <= TEST_END:
            result["test"].append(row)
        elif day >= "2026-01-01":
            continue  # intentionally untouched by this study path
        else:
            raise ValueError(f"row outside fixed study periods: {day}")
    for values in result.values():
        values.sort(key=_timestamp)
    return result


def expanding_embargo_folds(rows: list[dict[str, Any]], *, horizon_minutes: int, folds: int = 3) -> list[TemporalFold]:
    """Expanding chronological folds with a label-horizon embargo.

    No caller can supply shuffled order: timestamps must already be monotonic.
    The final training timestamp is strictly earlier than validation start minus
    the label horizon, preventing a forward label from crossing the fold edge.
    """
    if horizon_minutes <= 0:
        raise ValueError("horizon_minutes must be positive")
    if any(_timestamp(rows[i]) > _timestamp(rows[i + 1]) for i in range(len(rows) - 1)):
        raise ValueError("random or non-chronological fold order is forbidden")
    if len(rows) < 4:
        return []
    chunks = min(folds, max(1, len(rows) // 2))
    boundaries = np.linspace(1, len(rows) - 1, chunks + 1, dtype=int)[1:]
    output: list[TemporalFold] = []
    for start, end in zip(boundaries[:-1], boundaries[1:]):
        if end <= start:
            continue
        validation_start = _timestamp(rows[start])
        cutoff = validation_start - timedelta(minutes=horizon_minutes)
        train = tuple(i for i in range(start) if _timestamp(rows[i]) < cutoff)
        validation = tuple(range(start, end))
        if train and validation:
            output.append(TemporalFold(train, validation, cutoff.isoformat()))
    return output


def select_validation_only(
    gold_path: Path,
    sealed_path: Path,
    *,
    thresholds: Iterable[float] = DEFAULT_THRESHOLDS,
    horizon_minutes: int | None = None,
    risk_rule: str = "highest_side_probability",
    decision_top_k: int | None = None,
    long_thresholds: Iterable[float] | None = None,
    short_thresholds: Iterable[float] | None = None,
    top_k_long: int | None = None,
    top_k_short: int | None = None,
    meta_label_min_probability: float | None = None,
    model_families: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Run challengers and write the immutable selection before any test score."""
    rows, input_identity = _load_gold(gold_path)
    parts = temporal_split(rows)
    if not parts["train"] or not parts["validation"]:
        raise ValueError("study requires non-empty 2020-2022 train and 2023-2024 validation rows")
    horizon = horizon_minutes or _horizon_from_contract(gold_path)
    coverage = _feature_coverage(parts["train"] + parts["validation"])
    top_k_long = top_k_long if top_k_long is not None else decision_top_k
    top_k_short = top_k_short if top_k_short is not None else decision_top_k
    long_threshold_values = list(long_thresholds if long_thresholds is not None else thresholds)
    short_threshold_values = list(short_thresholds if short_thresholds is not None else thresholds)
    decision_rule = "cross_sectional_side_top_k" if top_k_long or top_k_short else "absolute_side_threshold"
    families = tuple(model_families or ("random", "momentum", "vwap_relative_strength", "sector_hist_gradient_boosting", "graph_hist_gradient_boosting", "side_hist_gradient_boosting"))
    choices = {"label_contract": input_identity["label_contract_id"], "model_family": families, "feature_family": list(FEATURE_COLUMNS), "feature_coverage": coverage, "side_rule": risk_rule, "calibration": "training_oof_isotonic_v1", "confidence_thresholds": list(thresholds), "long_thresholds": long_threshold_values, "short_thresholds": short_threshold_values, "risk_rule": risk_rule, "horizon_minutes": horizon, "decision_rule": decision_rule, "decision_top_k": decision_top_k, "top_k_long": top_k_long, "top_k_short": top_k_short, "meta_label_min_probability": meta_label_min_probability}
    experiment_id = _digest({"format": STUDY_FORMAT, "input": input_identity, "selection_inputs": choices})
    candidates: list[dict[str, Any]] = []
    for family in choices["model_family"]:
        oof = _oof_scores(family, parts["train"], horizon)
        calibrator = _fit_calibrator(oof[0], oof[1])
        raw_validation = _raw_scores(family, parts["train"], parts["validation"])
        calibrated = _calibrate(calibrator, raw_validation)
        feature_eligible, feature_reasons = _family_feature_eligible(family, coverage)
        if meta_label_min_probability is not None:
            meta_train = _sample_rows(parts["train"], 75_000)
            calibrated_train = _calibrate(calibrator, _raw_scores(family, parts["train"], meta_train))
            meta_model = _fit_meta_filter(meta_train, calibrated_train, min(map(float, long_threshold_values)), min(map(float, short_threshold_values)), top_k_long, top_k_short)
        else:
            meta_model = None
        for long_threshold in long_threshold_values:
            for short_threshold in short_threshold_values:
                metrics = score_predictions(parts["validation"], calibrated, float(long_threshold), top_k=decision_top_k, long_threshold=float(long_threshold), short_threshold=float(short_threshold), top_k_long=top_k_long, top_k_short=top_k_short, meta_model=meta_model, meta_min_probability=meta_label_min_probability)
                robust, reasons = _robust_validation_eligible(metrics, len(parts["validation"]))
                candidates.append({"label_contract": input_identity["label_contract_id"], "model_family": family, "feature_family": list(FEATURE_COLUMNS), "feature_eligible": feature_eligible, "rejection_reasons": feature_reasons + reasons, "robust_validation_eligible": feature_eligible and robust, "side_rule": risk_rule, "calibration": "training_oof_isotonic_v1", "confidence_threshold": max(float(long_threshold), float(short_threshold)), "long_threshold": float(long_threshold), "short_threshold": float(short_threshold), "risk_rule": risk_rule, "meta_label_min_probability": meta_label_min_probability, "validation": metrics})
    # A non-robust candidate is never promoted. When no candidate clears the
    # robustness constraints (concentration, breadth, payoff, CI), the study is
    # not_tradable and no model is selected; the sealed 2025 test must not run.
    eligible = [candidate for candidate in candidates if candidate.get("robust_validation_eligible")]
    selected = max(eligible, key=_candidate_rank) if eligible else None
    tradability = "tradable_candidate" if selected is not None else "not_tradable"
    selection_reason = None if selected is not None else "no_robust_validation_eligible_candidate"
    payload = {"format": STUDY_FORMAT, "experiment_id": experiment_id, "input_identity": input_identity, "periods": {"train": [TRAIN_START, TRAIN_END], "validation": [VALIDATION_START, VALIDATION_END], "test": [TEST_START, TEST_END]}, "selection_inputs": choices, "folds": [fold.__dict__ for fold in expanding_embargo_folds(parts["train"], horizon_minutes=horizon)], "candidates": candidates, "selected": selected, "tradability": tradability, "selection_reason": selection_reason, "test_accessed": False}
    _write_immutable(sealed_path, payload)
    return {"experiment_id": experiment_id, "sealed_selection": str(sealed_path), "selected": selected, "tradability": tradability, "selection_reason": selection_reason, "validation_candidates": len(candidates)}


def evaluate_sealed_test(gold_path: Path, sealed_path: Path, result_path: Path) -> dict[str, Any]:
    """Score 2025 exactly once for an immutable, matching sealed selection."""
    sealed = _read_json(sealed_path)
    if sealed.get("format") != STUDY_FORMAT or sealed.get("test_accessed") is not False:
        raise ValueError("invalid sealed validation-only selection artifact")
    if sealed.get("tradability") == "not_tradable" or sealed.get("selected") is None:
        raise ValueError("sealed selection is not_tradable; the 2025 test must not be spent on a non-robust candidate")
    rows, input_identity = _load_gold(gold_path)
    if sealed.get("input_identity") != input_identity:
        raise ValueError("selection identity does not match gold artifact; test reuse is forbidden")
    if result_path.exists():
        existing = _read_json(result_path)
        if existing.get("experiment_id") == sealed.get("experiment_id"):
            raise ValueError("2025 test result already exists for this experiment identity")
        raise ValueError("result path is already occupied by another experiment")
    parts = temporal_split(rows)
    if not parts["test"]:
        raise ValueError("sealed evaluation requires non-empty 2025 test rows")
    choice = sealed["selected"]
    # Selection is recorded before this fit. Calibration remains OOF training-only.
    oof = _oof_scores(str(choice["model_family"]), parts["train"], int(sealed["selection_inputs"]["horizon_minutes"]))
    calibrator = _fit_calibrator(oof[0], oof[1])
    fit_rows = parts["train"] + parts["validation"]
    raw_test = _raw_scores(str(choice["model_family"]), fit_rows, parts["test"])
    raw_train = _raw_scores(str(choice["model_family"]), parts["train"], parts["train"])
    train_probabilities = _calibrate(calibrator, raw_train)
    inputs = sealed["selection_inputs"]
    long_threshold = float(choice.get("long_threshold", choice["confidence_threshold"]))
    short_threshold = float(choice.get("short_threshold", choice["confidence_threshold"]))
    meta_min = choice.get("meta_label_min_probability", inputs.get("meta_label_min_probability"))
    meta_model = _fit_meta_filter(parts["train"], train_probabilities, long_threshold, short_threshold, inputs.get("top_k_long"), inputs.get("top_k_short")) if meta_min is not None else None
    metrics = score_predictions(parts["test"], _calibrate(calibrator, raw_test), float(choice["confidence_threshold"]), top_k=sealed["selection_inputs"].get("decision_top_k"), long_threshold=long_threshold, short_threshold=short_threshold, top_k_long=inputs.get("top_k_long"), top_k_short=inputs.get("top_k_short"), meta_model=meta_model, meta_min_probability=meta_min)
    payload = {"format": "meridian.sealed-test-result.v1", "experiment_id": sealed["experiment_id"], "selection_sha256": _sha256_file(sealed_path), "test_period": [TEST_START, TEST_END], "selected": choice, "test": metrics, "retrained_on": [TRAIN_START, VALIDATION_END], "calibration": "training_oof_isotonic_v1"}
    _write_immutable(result_path, payload)
    return {"experiment_id": sealed["experiment_id"], "result": str(result_path), "test": metrics}


def score_predictions(rows: list[dict[str, Any]], probabilities: list[tuple[float, float]], threshold: float, top_k: int | None = None, *, long_threshold: float | None = None, short_threshold: float | None = None, top_k_long: int | None = None, top_k_short: int | None = None, meta_model: Any | None = None, meta_min_probability: float | None = None) -> dict[str, Any]:
    assigned = _assign_actions(rows, probabilities, threshold, top_k, long_threshold=long_threshold, short_threshold=short_threshold, top_k_long=top_k_long, top_k_short=top_k_short)
    pre_filter = _score_assigned_actions(rows, probabilities, assigned)
    if meta_model is not None and meta_min_probability is not None:
        assigned = _apply_meta_filter(rows, probabilities, assigned, meta_model, float(meta_min_probability))
        metrics = _score_assigned_actions(rows, probabilities, assigned)
        metrics["pre_filter"] = pre_filter
        metrics["meta_filter"] = {"enabled": True, "min_probability": float(meta_min_probability), "filtered_trades": int(pre_filter["long"]) + int(pre_filter["short"]) - int(metrics["long"]) - int(metrics["short"])}
        return metrics
    pre_filter["meta_filter"] = {"enabled": False}
    return pre_filter


def _score_assigned_actions(rows: list[dict[str, Any]], probabilities: list[tuple[float, float]], assigned: list[str]) -> dict[str, Any]:
    actions, actuals, trade_returns, confidences, trade_rows, trade_sides = [], [], [], [], [], []
    for index, (row, (long_p, short_p)) in enumerate(zip(rows, probabilities)):
        action = assigned[index]
        actual = _actual_action(row)
        actions.append(action); actuals.append(actual)
        if action != "NO_TRADE":
            trade_returns.append(float(row.get("long_net_return_bps", 0) if action == "LONG" else row.get("short_net_return_bps", 0)))
            confidences.append((max(long_p, short_p), 1.0 if action == actual else 0.0))
            trade_rows.append(row)
            trade_sides.append(action.lower())
    total, trades = len(rows), len(trade_returns)
    directional = sum(a == b for a, b in zip(actions, actuals)) / total if total else 0.0
    precision, recall, macro_f1, balanced = _classification_metrics(actions, actuals)
    probabilities_flat = [max(pair) for pair in probabilities]
    outcomes_flat = [1.0 if _actual_action(row) != "NO_TRADE" else 0.0 for row in rows]
    side_results = {}
    for side in ("LONG", "SHORT", "NO_TRADE"):
        indices = [i for i, action in enumerate(actions) if action == side]
        side_returns = [float(rows[i].get("long_net_return_bps", 0) if side == "LONG" else rows[i].get("short_net_return_bps", 0)) for i in indices] if side != "NO_TRADE" else []
        side_rows = [rows[i] for i in indices] if side != "NO_TRADE" else []
        side_symbols = [str(row.get("symbol", "")) for row in side_rows]
        side_economics = economic_metrics(side_returns)
        side_results[side.lower()] = {"count": len(indices), "coverage": len(indices) / total if total else 0.0, "hit_rate": sum(value > 0 for value in side_returns) / len(side_returns) if side_returns else 0.0, "expected_value_bps": sum(side_returns) / len(side_returns) if side_returns else 0.0, "distinct_symbols": len(set(side_symbols)), "max_symbol_trade_share": max((side_symbols.count(symbol) / len(side_symbols) for symbol in set(side_symbols)), default=0.0) if side_symbols else 0.0, "cost_scenarios": _cost_scenarios(side_rows, [side.lower()] * len(side_rows)), "execution_scenarios": execution_quality_scenarios(side_rows, [side.lower()] * len(side_rows)), **{f"net_{key}": value for key, value in side_economics.items()}}
    economics = economic_metrics(trade_returns)
    trade_days = {str(row["timestamp"])[:10] for row in trade_rows}
    symbols = [str(row.get("symbol", "")) for row in trade_rows]
    max_symbol_share = max((symbols.count(symbol) / trades for symbol in set(symbols)), default=0.0) if trades else 0.0
    side_trade_share = max(actions.count("LONG"), actions.count("SHORT")) / trades if trades else 0.0
    execution_caveats = [SHORT_EXECUTION_CAVEAT] if actions.count("SHORT") and (not actions.count("LONG") or actions.count("SHORT") / trades >= 0.85) else []
    return {"rows": total, "long": actions.count("LONG"), "short": actions.count("SHORT"), "no_trade": actions.count("NO_TRADE"), "side_results": side_results, "trade_coverage": trades / total if total else 0.0, "trade_conditioned_hit_rate": sum(value > 0 for value in trade_returns) / trades if trades else 0.0, "directional_accuracy": directional, "macro_f1": macro_f1, "balanced_accuracy": balanced, "precision": precision, "recall": recall, "brier_score": _brier(probabilities_flat, outcomes_flat), "ece": _ece(probabilities_flat, outcomes_flat), "expected_value_bps": sum(trade_returns) / trades if trades else 0.0, "total_net_bps": sum(trade_returns), "trading_days": len(trade_days), "max_symbol_trade_share": max_symbol_share, "distinct_symbols": len(set(symbols)), "max_side_trade_share": side_trade_share, "execution_caveats": execution_caveats, **{f"net_{key}": value for key, value in economics.items()}, "cost_sensitivity": {str(cost): _scenario_expectancy(trade_rows, trade_sides, float(cost)) for cost in (8, 12, 16, 20)}, "cost_scenarios": _cost_scenarios(trade_rows, trade_sides), "execution_scenarios": execution_quality_scenarios(trade_rows, trade_sides)}


def _raw_scores(family: str, train: list[dict[str, Any]], target: list[dict[str, Any]]) -> list[tuple[float, float]]:
    if family == "random":
        return [(_stable_probability(row, "long"), _stable_probability(row, "short")) for row in target]
    if family in {"momentum", "vwap_relative_strength"}:
        def score(row: dict[str, Any]) -> tuple[float, float]:
            value = float(row.get("return_3", row.get("return_1", 0)) or 0)
            if family == "vwap_relative_strength": value += float(row.get("vwap_distance", 0) or 0) + float(row.get("relative_strength", 0) or 0)
            p = 1.0 / (1.0 + np.exp(-20 * value))
            return float(p), float(1 - p)
        return [score(row) for row in target]
    if family not in {"sector_hist_gradient_boosting", "graph_hist_gradient_boosting", "side_hist_gradient_boosting"}:
        raise ValueError(f"unknown challenger: {family}")
    x_train, x_target = _features(train), _features(target)
    long_scores = _fit_binary(x_train, [_long_success(row) for row in train], x_target)
    short_scores = _fit_binary(x_train, [_short_success(row) for row in train], x_target)
    return list(zip(long_scores, short_scores))


def _oof_scores(family: str, train: list[dict[str, Any]], horizon: int) -> tuple[list[tuple[float, float]], list[float]]:
    folds = expanding_embargo_folds(train, horizon_minutes=horizon)
    predictions: list[tuple[float, float]] = []
    outcomes: list[tuple[int, int]] = []
    for fold in folds:
        fitting = [train[i] for i in fold.train_indices]; holdout = [train[i] for i in fold.validation_indices]
        predictions.extend(_raw_scores(family, fitting, holdout))
        outcomes.extend((_long_success(row), _short_success(row)) for row in holdout)
    if not predictions:  # tiny fixture fallback is still training-only, never validation/test
        predictions = _raw_scores(family, train, train)
        outcomes = [(_long_success(row), _short_success(row)) for row in train]
    return predictions, outcomes


def _fit_binary(x: np.ndarray, y: list[int], target: np.ndarray) -> list[float]:
    if not y or len(set(y)) < 2:
        return [float(y[0]) if y else 0.0] * len(target)
    model = HistGradientBoostingClassifier(max_iter=40, max_leaf_nodes=8, learning_rate=0.08, random_state=42)
    model.fit(x, y)
    return model.predict_proba(target)[:, 1].astype(float).tolist()


def _fit_calibrator(scores: list[tuple[float, float]], outcomes: list[tuple[int, int]]) -> tuple[Any, Any]:
    """Fit independent side calibrators from training-only OOF predictions."""
    raw = np.array(scores, dtype=float)
    long_y, short_y = np.array(outcomes, dtype=float).T
    long = IsotonicRegression(out_of_bounds="clip").fit(raw[:, 0], long_y) if len(set(long_y.tolist())) > 1 else None
    short = IsotonicRegression(out_of_bounds="clip").fit(raw[:, 1], short_y) if len(set(short_y.tolist())) > 1 else None
    return long, short


def _calibrate(calibrator: tuple[Any, Any], scores: list[tuple[float, float]]) -> list[tuple[float, float]]:
    first, second = calibrator
    raw = np.array(scores, dtype=float)
    long = first.predict(raw[:, 0]).astype(float).tolist() if first is not None else raw[:, 0].astype(float).tolist()
    short = second.predict(raw[:, 1]).astype(float).tolist() if second is not None else raw[:, 1].astype(float).tolist()
    return list(zip(long, short))


def _features(rows: list[dict[str, Any]]) -> np.ndarray:
    _assert_v1_feature_columns(FEATURE_COLUMNS)
    return np.array([[float(row.get(key, 0) or 0) for key in FEATURE_COLUMNS] for row in rows], dtype=float)
def _assert_v1_feature_columns(columns: Iterable[str]) -> None:
    blocked = [
        column for column in columns
        if any(token in column.lower() for token in BLOCKED_FEATURE_COLUMN_TOKENS)
    ]
    if blocked:
        raise ValueError(f"blocked V1 feature column(s): {', '.join(sorted(blocked))}")
def _sample_rows(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if len(rows) <= limit:
        return rows
    step = max(1, len(rows) // limit)
    return rows[::step][:limit]
def _fit_meta_filter(rows: list[dict[str, Any]], probabilities: list[tuple[float, float]], long_threshold: float, short_threshold: float, top_k_long: int | None, top_k_short: int | None) -> Any | None:
    if len(rows) > 250_000:
        step = max(1, len(rows) // 250_000)
        rows = rows[::step]
        probabilities = probabilities[::step]
    actions = _assign_actions(rows, probabilities, long_threshold, long_threshold=long_threshold, short_threshold=short_threshold, top_k_long=top_k_long, top_k_short=top_k_short)
    features, labels = [], []
    recent: dict[tuple[str, str], list[int]] = defaultdict(list)
    for row, probs, action in zip(rows, probabilities, actions):
        if action == "NO_TRADE":
            continue
        features.append(_meta_features(row, probs, action, recent))
        win = int(action == _actual_action(row))
        labels.append(win)
        key = (str(row.get("symbol", "")), action)
        recent[key].append(win)
        if len(recent[key]) > 20:
            del recent[key][0]
    if len(features) < 10 or len(set(labels)) < 2:
        return None
    model = HistGradientBoostingClassifier(max_iter=30, max_leaf_nodes=6, learning_rate=0.08, random_state=99)
    model.fit(np.array(features, dtype=float), labels)
    return model


def _apply_meta_filter(rows: list[dict[str, Any]], probabilities: list[tuple[float, float]], actions: list[str], model: Any, min_probability: float) -> list[str]:
    output = list(actions)
    recent: dict[tuple[str, str], list[int]] = defaultdict(list)
    feature_rows, feature_indices = [], []
    for index, (row, probs, action) in enumerate(zip(rows, probabilities, actions)):
        if action == "NO_TRADE":
            continue
        feature_rows.append(_meta_features(row, probs, action, recent))
        feature_indices.append(index)
        win = int(action == _actual_action(row))
        key = (str(row.get("symbol", "")), action)
        recent[key].append(win)
        if len(recent[key]) > 20:
            del recent[key][0]
    if feature_rows:
        probabilities_meta = np.asarray(model.predict_proba(np.array(feature_rows, dtype=float)))[:, 1]
        for index, probability in zip(feature_indices, probabilities_meta):
            if float(probability) < min_probability:
                output[index] = "NO_TRADE"
    return output


def _meta_features(row: dict[str, Any], probabilities: tuple[float, float], action: str, recent: dict[tuple[str, str], list[int]]) -> list[float]:
    side = action.lower()
    long_p, short_p = probabilities
    target = float(row.get(f"{side}_target_net_bps", row.get(f"{side}_target_bps", 0)) or 0)
    loss = float(row.get(f"{side}_stop_net_loss_bps", row.get(f"{side}_stop_loss_bps", 0)) or 0)
    edge = _expected_edge(long_p if action == "LONG" else short_p, row, side)
    history = recent.get((str(row.get("symbol", "")), action), [])
    phase = str(row.get("session_phase", "mid")).lower()
    return [
        1.0 if action == "LONG" else 0.0,
        float(long_p),
        float(short_p),
        float(max(long_p, short_p)),
        float(edge),
        float(row.get("rolling_volatility", 0) or 0),
        float(row.get("atr_bps", 0) or 0),
        float(row.get("volume_ratio", 1) or 1),
        float(row.get("intraday_range_position", 0.5) or 0.5),
        target,
        loss,
        target / loss if loss else 0.0,
        sum(history[-10:]) / len(history[-10:]) if history else 0.5,
        min(len(history), 20) / 20.0,
        1.0 if phase == "open" else 0.0,
        1.0 if phase == "close" else 0.0,
        (int(_digest(str(row.get("symbol", "")))[:8], 16) % 997) / 997.0,
    ]


def _assign_actions(rows: list[dict[str, Any]], probabilities: list[tuple[float, float]], threshold: float, top_k: int | None = None, *, long_threshold: float | None = None, short_threshold: float | None = None, top_k_long: int | None = None, top_k_short: int | None = None) -> list[str]:
    """Map per-row probabilities to LONG/SHORT/NO_TRADE actions.

    Absolute mode (``top_k`` falsy) keeps the per-row threshold gate. Cross
    sectional mode ranks every symbol active at the same decision bar by its
    expected edge and recommends only the top ``k`` longs and top ``k`` shorts,
    with the threshold acting as a permissive floor. This is the recommendation
    product: a few comparable best ideas per bar, not every row that clears a
    fixed bar, which structurally bounds single-symbol concentration.
    """
    long_threshold = threshold if long_threshold is None else long_threshold
    short_threshold = threshold if short_threshold is None else short_threshold
    top_k_long = top_k if top_k_long is None else top_k_long
    top_k_short = top_k if top_k_short is None else top_k_short
    if not top_k_long and not top_k_short:
        return [_choose_action(row, long_p, short_p, threshold, long_threshold=long_threshold, short_threshold=short_threshold) for row, (long_p, short_p) in zip(rows, probabilities)]
    groups: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        groups.setdefault(str(row.get("timestamp")), []).append(index)
    actions = ["NO_TRADE"] * len(rows)
    for indices in groups.values():
        longs, shorts = [], []
        for index in indices:
            long_p, short_p = probabilities[index]
            long_edge = _expected_edge(long_p, rows[index], "long")
            short_edge = _expected_edge(short_p, rows[index], "short")
            if top_k_long and long_p >= long_threshold and long_edge > 0:
                longs.append((long_edge, index))
            if top_k_short and short_p >= short_threshold and short_edge > 0:
                shorts.append((short_edge, index))
        longs.sort(key=lambda item: (-item[0], item[1]))
        shorts.sort(key=lambda item: (-item[0], item[1]))
        long_edges = {index: edge for edge, index in longs}
        short_edges = {index: edge for edge, index in shorts}
        top_long = {index for _, index in longs[:top_k_long or 0]}
        top_short = {index for _, index in shorts[:top_k_short or 0]}
        for index in indices:
            in_long, in_short = index in top_long, index in top_short
            if in_long and (not in_short or long_edges.get(index, 0.0) >= short_edges.get(index, 0.0)):
                actions[index] = "LONG"
            elif in_short:
                actions[index] = "SHORT"
    return actions


def _choose_action(row: dict[str, Any], long_p: float, short_p: float, threshold: float, *, long_threshold: float | None = None, short_threshold: float | None = None) -> str:
    long_threshold = threshold if long_threshold is None else long_threshold
    short_threshold = threshold if short_threshold is None else short_threshold
    long_edge = _expected_edge(long_p, row, "long")
    short_edge = _expected_edge(short_p, row, "short")
    if long_p >= long_threshold and long_edge > 0 and long_edge >= short_edge:
        return "LONG"
    if short_p >= short_threshold and short_edge > 0 and short_edge > long_edge:
        return "SHORT"
    return "NO_TRADE"
def _expected_edge(probability: float, row: dict[str, Any], side: str) -> float:
    target = float(row.get(f"{side}_target_net_bps", 0) or 0)
    loss = float(row.get(f"{side}_stop_net_loss_bps", 0) or 0)
    cost = float(row.get("cost_bps", 8) or 8)
    if target <= 0 or loss <= 0:
        close = float(row.get("entry_price", row.get("close", 0)) or 0)
        atr = float(row.get("atr", 0) or 0)
        if close > 0 and atr > 0:
            target = max(0.75 * atr / close * 10000 - cost, 0.0)
            loss = 0.50 * atr / close * 10000 + cost
    if target <= 0 or loss <= 0:
        return probability - 0.5
    return probability * target - (1.0 - probability) * loss
def _scenario_expectancy(rows: list[dict[str, Any]], sides: list[str], cost_bps: float) -> float:
    if not rows:
        return 0.0
    returns = [float(row.get(f"{side}_gross_return_bps", row.get(f"{side}_return_bps", 0)) or 0) - cost_bps for row, side in zip(rows, sides)]
    return sum(returns) / len(returns)
def _cost_scenarios(rows: list[dict[str, Any]], sides: list[str]) -> dict[str, dict[str, float]]:
    output: dict[str, dict[str, float]] = {}
    for name, cost in COST_SCENARIOS.items():
        values = [float(row.get(f"{side}_gross_return_bps", row.get(f"{side}_return_bps", 0)) or 0) - cost for row, side in zip(rows, sides)]
        economics = economic_metrics(values)
        payoff = float(economics.get("payoff_ratio", 0.0))
        output[name] = {
            "cost_bps": cost,
            "trades": len(values),
            "hit_rate": sum(value > 0 for value in values) / len(values) if values else 0.0,
            "expected_value_bps": sum(values) / len(values) if values else 0.0,
            "profit_factor": float(economics.get("profit_factor", 0.0)),
            "expectancy_ci95_low_bps": float(economics.get("expectancy_ci95_low_bps", 0.0)),
            "max_drawdown_bps": float(economics.get("max_drawdown_bps", 0.0)),
            "breakeven_hit_rate": 1.0 / (1.0 + payoff) if payoff > 0 else 1.0,
        }
    return output
def _feature_coverage(rows: list[dict[str, Any]]) -> dict[str, dict[str, float | bool]]:
    sample = rows[: min(len(rows), 20_000)]
    output: dict[str, dict[str, float | bool]] = {}
    for column in FEATURE_COLUMNS + ("volume",):
        present = sum(column in row and row.get(column) not in (None, "") for row in sample)
        values = [float(row.get(column, 0) or 0) for row in sample if column in row and row.get(column) not in (None, "")]
        non_zero = sum(value != 0 for value in values)
        output[column] = {"present_rate": present / len(sample) if sample else 0.0, "non_zero_rate": non_zero / len(values) if values else 0.0, "usable": bool(values) and (column in {"volume", "volume_ratio", "atr"} or non_zero > 0)}
    return output
def _family_feature_eligible(family: str, coverage: dict[str, dict[str, float | bool]]) -> tuple[bool, list[str]]:
    reasons = []
    for column in FAMILY_REQUIREMENTS.get(family, ()):
        item = coverage.get(column, {})
        if float(item.get("present_rate", 0.0)) < 0.95:
            reasons.append(f"missing_feature:{column}")
        elif not bool(item.get("usable", False)):
            reasons.append(f"degenerate_feature:{column}")
    return not reasons, reasons
def _robust_validation_eligible(metrics: dict[str, Any], validation_rows: int) -> tuple[bool, list[str]]:
    large = validation_rows >= ROBUST_REGIME_ROW_THRESHOLD
    min_trades = ROBUST_MIN_VALIDATION_TRADES if large else 1
    min_days = ROBUST_MIN_VALIDATION_DAYS if large else 1
    trades = int(metrics["long"]) + int(metrics["short"])
    # Concentration and breadth are hard constraints only in the production-scale
    # regime; tiny fixtures cannot satisfy breadth and are exempted, consistent
    # with the relaxed trade-count and trading-day floors above.
    checks = {
        "validation_trade_count": trades >= min_trades,
        "validation_trading_days": int(metrics["trading_days"]) >= min_days,
        "validation_expectancy_positive": float(metrics["expected_value_bps"]) > 0,
        "validation_ci_positive": float(metrics["net_expectancy_ci95_low_bps"]) > 0 if trades >= min_trades else float(metrics["expected_value_bps"]) > 0,
        "validation_profit_factor": float(metrics["net_profit_factor"]) >= 1.05,
        "symbol_concentration": float(metrics["max_symbol_trade_share"]) <= ROBUST_MAX_SYMBOL_TRADE_SHARE if large else True,
        "min_distinct_symbols": int(metrics.get("distinct_symbols", 0)) >= ROBUST_MIN_DISTINCT_SYMBOLS if large else True,
    }
    if large and "full_cost" in metrics.get("cost_scenarios", {}):
        full_cost = metrics["cost_scenarios"]["full_cost"]
        checks["validation_full_cost_expectancy_positive"] = float(full_cost.get("expected_value_bps", 0.0)) > 0
        checks["validation_full_cost_ci_positive"] = float(full_cost.get("expectancy_ci95_low_bps", 0.0)) > 0
        checks["validation_full_cost_profit_factor"] = float(full_cost.get("profit_factor", 0.0)) >= 1.05
    for side in ("long", "short"):
        item = metrics.get("side_results", {}).get(side, {})
        count = int(item.get("count", 0) or 0)
        if not count:
            continue
        checks[f"{side}_trade_count"] = count >= min_trades
        checks[f"{side}_expectancy_positive"] = float(item.get("expected_value_bps", 0.0)) > 0
        checks[f"{side}_ci_positive"] = float(item.get("net_expectancy_ci95_low_bps", item.get("expected_value_bps", 0.0))) > 0
        checks[f"{side}_profit_factor"] = float(item.get("net_profit_factor", 0.0)) >= 1.05
        checks[f"{side}_symbol_concentration"] = float(item.get("max_symbol_trade_share", 0.0)) <= ROBUST_MAX_SYMBOL_TRADE_SHARE if large else True
        checks[f"{side}_min_distinct_symbols"] = int(item.get("distinct_symbols", 0)) >= ROBUST_MIN_DISTINCT_SYMBOLS if large else True
        if large and "full_cost" in item.get("cost_scenarios", {}):
            full_cost = item["cost_scenarios"]["full_cost"]
            checks[f"{side}_full_cost_expectancy_positive"] = float(full_cost.get("expected_value_bps", 0.0)) > 0
            checks[f"{side}_full_cost_ci_positive"] = float(full_cost.get("expectancy_ci95_low_bps", 0.0)) > 0
            checks[f"{side}_full_cost_profit_factor"] = float(full_cost.get("profit_factor", 0.0)) >= 1.05
    reasons = [name for name, passed in checks.items() if not passed]
    return not reasons, reasons
def _candidate_rank(item: dict[str, Any]) -> tuple[float, float, float, float, float, float, str]:
    metrics = item["validation"]
    eligible = 1.0 if item.get("robust_validation_eligible") else 0.0
    trades = float(metrics["long"]) + float(metrics["short"])
    full_cost = metrics.get("cost_scenarios", {}).get("full_cost", {})
    ci_low = float(full_cost.get("expectancy_ci95_low_bps", metrics.get("net_expectancy_ci95_low_bps", 0.0)))
    profit_factor = float(full_cost.get("profit_factor", metrics.get("net_profit_factor", 0.0)))
    expected_value = float(full_cost.get("expected_value_bps", metrics.get("expected_value_bps", 0.0)))
    concentration = float(metrics.get("max_symbol_trade_share", 1.0))
    return (
        eligible,
        ci_low,
        profit_factor,
        expected_value,
        trades,
        -concentration,
        str(item.get("model_family", "")),
    )
def _long_success(row: dict[str, Any]) -> int: return int(float(row.get("long_net_return_bps", 0) or 0) > 0)
def _short_success(row: dict[str, Any]) -> int: return int(float(row.get("short_net_return_bps", 0) or 0) > 0)
def _actual_action(row: dict[str, Any]) -> str: return "LONG" if _long_success(row) else "SHORT" if _short_success(row) else "NO_TRADE"
def _day(row: dict[str, Any]) -> str: return str(row["timestamp"])[:10]
def _timestamp(row: dict[str, Any]) -> datetime: return datetime.fromisoformat(str(row["timestamp"]))
def _stable_probability(row: dict[str, Any], side: str) -> float: return int(_digest([row.get("symbol"), row.get("timestamp"), side])[:8], 16) / 0xFFFFFFFF
def _brier(p: list[float], y: list[float]) -> float: return float(np.mean((np.array(p) - np.array(y)) ** 2)) if p else 0.0
def _ece(p: list[float], y: list[float], bins: int = 10) -> float:
    if not p: return 0.0
    a, b = np.array(p), np.array(y); total = 0.0
    for lower in np.linspace(0, 0.9, bins):
        mask = (a >= lower) & (a < lower + 0.1 if lower < 0.9 else a <= 1)
        if mask.any(): total += float(mask.mean() * abs(a[mask].mean() - b[mask].mean()))
    return total
def _classification_metrics(pred: list[str], actual: list[str]) -> tuple[float, float, float, float]:
    ps, rs = [], []
    for label in ("LONG", "SHORT", "NO_TRADE"):
        tp = sum(a == label and b == label for a, b in zip(pred, actual)); fp = sum(a == label and b != label for a, b in zip(pred, actual)); fn = sum(a != label and b == label for a, b in zip(pred, actual))
        ps.append(tp / (tp + fp) if tp + fp else 0.0); rs.append(tp / (tp + fn) if tp + fn else 0.0)
    f1 = [2*p*r/(p+r) if p+r else 0.0 for p, r in zip(ps, rs)]
    return sum(ps)/3, sum(rs)/3, sum(f1)/3, sum(rs)/3


def _load_gold(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = Path(path)
    rows = pl.read_parquet(path).to_dicts() if path.suffix == ".parquet" else pl.read_csv(path).to_dicts()
    rows = [row for row in rows if not bool(row.get("label_excluded", False))]
    manifest = path.with_suffix(".manifest.json")
    contract = path.with_suffix(".label-contract.json")
    return rows, {"gold_sha256": _sha256_file(path), "gold_id": _read_json(manifest).get("gold_id") if manifest.exists() else None, "label_contract_id": _read_json(contract).get("contract_id") if contract.exists() else None}
def _horizon_from_contract(path: Path) -> int:
    contract = path.with_suffix(".label-contract.json")
    return int(_read_json(contract).get("contract", {}).get("timeout_minutes", 30)) if contract.exists() else 30
def _digest(value: Any) -> str: return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
def _sha256_file(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def _read_json(path: Path) -> dict[str, Any]: return json.loads(path.read_text(encoding="utf-8"))
def _write_immutable(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if _read_json(path) != payload: raise ValueError(f"immutable artifact already exists with different content: {path}")
        return
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
