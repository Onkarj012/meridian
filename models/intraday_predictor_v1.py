"""Frozen V1 direction and normalized-magnitude LightGBM models."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from models.intraday_confidence import (
    CorrectnessCalibrator,
    calibrate_confidence,
    correctness_labels,
    fit_correctness_calibrator,
)
from models.intraday_predictor import (
    CLASS_ORDER,
    NUM_THREADS,
    SEED,
    HorizonModels as V0HorizonModels,
    fit_horizon_models as fit_v0_horizon_models,
)


@dataclass
class HorizonModels:
    """A V1 classifier and one quantile magnitude model for a horizon."""

    horizon: int
    feature_columns: list[str]
    classifier: Any
    regressor: Any
    direction_models: V0HorizonModels | None = None

    @property
    def magnitude_regressor(self) -> Any:
        return self.regressor

    def as_dict(self) -> dict[str, Any]:
        return {
            "horizon": self.horizon,
            "feature_columns": self.feature_columns,
            "classifier": self.classifier,
            "regressor": self.regressor,
            "direction_models": self.direction_models,
        }


def fit_horizon_models(
    features: pd.DataFrame,
    targets: pd.DataFrame,
    train_rows: Sequence[bool] | pd.Series | np.ndarray,
    horizon: int,
    *,
    n_estimators: int = 350,
    seed: int = SEED,
    num_threads: int = NUM_THREADS,
) -> HorizonModels:
    """Fit frozen v0 direction plus a V1 q50 normalized-magnitude model."""
    mask = np.asarray(train_rows, dtype=bool)
    if len(mask) != len(features):
        raise ValueError("train_rows must align with features")
    direction_column = f"target_h{horizon}_dir"
    magnitude_column = f"target_h{horizon}_normalized_magnitude"
    if direction_column not in targets or magnitude_column not in targets:
        raise ValueError(f"targets need {direction_column!r} and {magnitude_column!r}")
    usable = mask & targets[direction_column].notna().to_numpy() & targets[magnitude_column].notna().to_numpy()
    if usable.sum() < 3:
        raise ValueError("training rows do not contain enough valid labels")

    # The v0 fitter is the single source of truth for the classifier's
    # multiclass architecture, class weighting, seeds, and deterministic mode.
    direction_targets = pd.DataFrame({
        direction_column: targets[direction_column],
        f"target_h{horizon}_mag_bps": targets[magnitude_column],
    }, index=targets.index)
    frozen = fit_v0_horizon_models(
        features,
        direction_targets,
        mask,
        horizon,
        n_estimators=n_estimators,
        seed=seed,
        num_threads=num_threads,
    )

    lgb = _lightgbm()
    quantile_params = frozen.regressor.get_params()
    quantile_params.update(objective="quantile", alpha=0.50)
    regressor = lgb.LGBMRegressor(**quantile_params)
    X = features.loc[usable].copy()
    y = pd.to_numeric(targets.loc[usable, magnitude_column], errors="raise").to_numpy(dtype=float)
    regressor.fit(X, y, categorical_feature=_categorical_columns(X))
    return HorizonModels(int(horizon), list(features.columns), frozen.classifier, regressor, frozen)


def predict_horizon(
    models: HorizonModels | dict[str, Any],
    features: pd.DataFrame,
    *,
    scale: pd.Series | np.ndarray | None = None,
    targets: pd.DataFrame | None = None,
    calibrator: CorrectnessCalibrator | dict[str, Any] | None = None,
    close: pd.Series | np.ndarray | None = None,
) -> pd.DataFrame:
    """Return direction probabilities and nonnegative absolute-move bps."""
    bundle = _bundle(models)
    X = features.reindex(columns=bundle.feature_columns).copy()
    probabilities = np.asarray(bundle.classifier.predict_proba(X), dtype=float)
    classes = np.asarray(getattr(bundle.classifier, "classes_", np.arange(probabilities.shape[1])), dtype=int)
    full = np.zeros((len(X), 3), dtype=float)
    for index, label in enumerate(classes):
        if 0 <= int(label) < 3:
            full[:, int(label)] = probabilities[:, index]
    labels = np.asarray(CLASS_ORDER)[full.argmax(axis=1)]
    normalized = np.asarray(bundle.regressor.predict(X), dtype=float)
    normalized = np.maximum(0.0, normalized)
    scale_values = _prediction_scale(scale, targets, bundle.horizon, len(X), features.index)
    magnitude = scale_values * normalized
    result = pd.DataFrame({
        "p_down_raw": full[:, 0],
        "p_flat_raw": full[:, 1],
        "p_up_raw": full[:, 2],
        "direction": labels,
        "median_abs_move_bps": magnitude,
    }, index=features.index)
    if calibrator is not None:
        result["confidence_correct"] = calibrate_confidence(calibrator, result)
    return result


def fit_confidence_calibrator(*args: Any, **kwargs: Any) -> CorrectnessCalibrator:
    """Use the unchanged v0 session-weighted confidence calibrator."""
    return fit_correctness_calibrator(*args, **kwargs)


def confidence_correctness_labels(*args: Any, **kwargs: Any) -> np.ndarray:
    """Use the unchanged v0 top-label correctness target."""
    return correctness_labels(*args, **kwargs)


def _bundle(models: HorizonModels | dict[str, Any]) -> HorizonModels:
    if isinstance(models, HorizonModels):
        return models
    return HorizonModels(**models)


def _prediction_scale(
    scale: pd.Series | np.ndarray | None,
    targets: pd.DataFrame | None,
    horizon: int,
    length: int,
    index: pd.Index,
) -> np.ndarray:
    if scale is None and targets is not None:
        column = f"target_h{horizon}_scale"
        if column not in targets:
            raise ValueError(f"targets need {column!r} to scale magnitude predictions")
        scale = targets[column]
    if scale is None:
        return np.ones(length, dtype=float)
    values = pd.to_numeric(pd.Series(scale, index=index), errors="raise").to_numpy(dtype=float)
    if len(values) != length:
        raise ValueError("scale must align with prediction features")
    if (values < 0).any():
        raise ValueError("scale must be nonnegative")
    return values


def _categorical_columns(frame: pd.DataFrame) -> list[str]:
    return [column for column in frame.columns if isinstance(frame[column].dtype, pd.CategoricalDtype)]


def _lightgbm() -> Any:
    import lightgbm as lgb
    return lgb


fit_horizon_models_v1 = fit_horizon_models
predict_horizon_v1 = predict_horizon
V1HorizonModels = HorizonModels
