"""Frozen v1.1 direction-only LightGBM models and confidence policy."""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier

from models.intraday_confidence import (
    CorrectnessCalibrator,
    calibrate_confidence,
    correctness_labels,
    fit_correctness_calibrator,
)
from models.intraday_predictor import CLASS_ORDER, NUM_THREADS, SEED


FROZEN_N_ESTIMATORS = 350
FROZEN_CLASSIFIER_PARAMS = {
    "learning_rate": 0.03,
    "num_leaves": 31,
    "min_child_samples": 400,
    "max_bin": 127,
    "feature_fraction": 0.80,
    "bagging_fraction": 0.80,
    "bagging_freq": 1,
    "reg_alpha": 0.5,
    "reg_lambda": 5.0,
    "objective": "multiclass",
    "num_class": 3,
    "deterministic": True,
    "force_col_wise": True,
    "verbosity": -1,
}


@dataclass
class DirectionHorizonModel:
    """One frozen classifier and its exact feature/label schema."""

    horizon: int
    feature_columns: list[str]
    label_column: str
    classifier: Any

    def as_dict(self) -> dict[str, Any]:
        return {
            "horizon": self.horizon,
            "feature_columns": self.feature_columns,
            "label_column": self.label_column,
            "classifier": self.classifier,
        }


def fit_direction_model(
    features: pd.DataFrame,
    targets: pd.DataFrame,
    train_rows: Sequence[bool] | pd.Series | np.ndarray,
    horizon: int,
    *,
    label_column: str | None = None,
    seed: int = SEED,
    num_threads: int = NUM_THREADS,
) -> DirectionHorizonModel:
    """Fit only the frozen v0 multiclass classifier architecture."""
    mask = np.asarray(train_rows, dtype=bool)
    if len(mask) != len(features):
        raise ValueError("train_rows must align with features")
    column = label_column or f"target_h{horizon}_smooth_dir"
    if column not in targets:
        raw = f"target_h{horizon}_dir"
        if raw in targets:
            column = raw
        else:
            raise ValueError(f"targets need {column!r}")
    labels = pd.Categorical(targets[column], categories=CLASS_ORDER).codes
    usable = mask & (labels >= 0)
    if int(usable.sum()) < 3:
        raise ValueError("training rows do not contain enough valid labels")
    X = features.iloc[np.flatnonzero(usable)].copy()
    y = labels[usable]
    if len(np.unique(y)) < 2:
        classifier = DummyClassifier(strategy="prior")
        classifier.fit(X, y)
    else:
        lgb = _lightgbm()
        counts = np.bincount(y, minlength=3).astype(float)
        present = counts > 0
        weights = np.ones(3, dtype=float)
        weights[present] = len(y) / (present.sum() * counts[present])
        weights = np.clip(weights, 0.5, 2.0)
        params = dict(FROZEN_CLASSIFIER_PARAMS)
        params.update(
            n_estimators=FROZEN_N_ESTIMATORS,
            random_state=int(seed),
            feature_fraction_seed=int(seed),
            bagging_seed=int(seed),
            data_random_seed=int(seed),
            n_jobs=int(num_threads),
            class_weight={index: float(weights[index]) for index in np.flatnonzero(present)},
        )
        classifier = lgb.LGBMClassifier(**params)
        classifier.fit(X, y, categorical_feature=_categorical_columns(X))
    return DirectionHorizonModel(int(horizon), list(features.columns), column, classifier)


def fit_horizon_models(*args: Any, **kwargs: Any) -> DirectionHorizonModel:
    """Compatibility alias for the direction-only v1.1 model API."""
    return fit_direction_model(*args, **kwargs)


def predict_direction(
    model: DirectionHorizonModel | dict[str, Any],
    features: pd.DataFrame,
    *,
    calibrator: CorrectnessCalibrator | dict[str, Any] | None = None,
) -> pd.DataFrame:
    """Return three-class probabilities, direction, and calibrated confidence."""
    bundle = _bundle(model)
    X = features.reindex(columns=bundle.feature_columns).copy()
    probabilities = np.asarray(bundle.classifier.predict_proba(X), dtype=float)
    classes = np.asarray(getattr(bundle.classifier, "classes_", np.arange(probabilities.shape[1])), dtype=int)
    full = np.zeros((len(X), 3), dtype=float)
    for index, label in enumerate(classes):
        if 0 <= int(label) < 3:
            full[:, int(label)] = probabilities[:, index]
    result = pd.DataFrame({
        "p_down_raw": full[:, 0],
        "p_flat_raw": full[:, 1],
        "p_up_raw": full[:, 2],
        "direction": np.asarray(CLASS_ORDER)[full.argmax(axis=1)],
    }, index=features.index)
    if calibrator is not None:
        result["confidence"] = calibrate_confidence(calibrator, result)
        result["confidence_correct"] = result["confidence"]
    else:
        result["confidence"] = full.max(axis=1)
    return result


predict_horizon = predict_direction


def fit_confidence_calibrator(
    probabilities: pd.DataFrame | np.ndarray,
    y_true: pd.Series | np.ndarray,
    sessions: pd.Series | np.ndarray,
    **kwargs: Any,
) -> CorrectnessCalibrator:
    """Fit the unchanged session-weighted isotonic/Platt calibration."""
    return fit_correctness_calibrator(probabilities, y_true, sessions, **kwargs)


def confidence_correctness_labels(*args: Any, **kwargs: Any) -> np.ndarray:
    """Return the frozen top-label correctness target."""
    return correctness_labels(*args, **kwargs)


def fit_confidence_thresholds(
    calibrated_confidence: Iterable[float] | pd.Series,
    *,
    primary_percentile: float = 75.0,
    secondary_percentile: float = 90.0,
) -> dict[str, float]:
    """Freeze the calibration-block 75th and 90th confidence percentiles."""
    values = pd.to_numeric(pd.Series(calibrated_confidence), errors="coerce").to_numpy(dtype=float)
    values = values[np.isfinite(values)]
    if not len(values):
        raise ValueError("calibration confidence contains no finite values")
    return {
        "primary": float(np.percentile(values, primary_percentile)),
        "secondary": float(np.percentile(values, secondary_percentile)),
        "primary_percentile": float(primary_percentile),
        "secondary_percentile": float(secondary_percentile),
    }


fit_thresholds = fit_confidence_thresholds


def frozen_classifier_params() -> dict[str, Any]:
    """Return the registered classifier settings for artifact manifests/tests."""
    result = dict(FROZEN_CLASSIFIER_PARAMS)
    result["n_estimators"] = FROZEN_N_ESTIMATORS
    result["seed"] = SEED
    result["num_threads"] = NUM_THREADS
    return result


def _bundle(model: DirectionHorizonModel | dict[str, Any]) -> DirectionHorizonModel:
    return model if isinstance(model, DirectionHorizonModel) else DirectionHorizonModel(**model)


def _categorical_columns(frame: pd.DataFrame) -> list[str]:
    return [column for column in frame.columns if isinstance(frame[column].dtype, pd.CategoricalDtype)]


def _lightgbm() -> Any:
    import lightgbm as lgb
    return lgb
