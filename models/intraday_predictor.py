"""Frozen LightGBM and linear baseline models for intraday horizons."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from features.intraday_prediction import REGIME_CATEGORIES


SEED = 20260718
NUM_THREADS = 8
CLASS_ORDER = ("DOWN", "FLAT", "UP")


@dataclass
class HorizonModels:
    """Trained primary and baseline models plus their frozen feature schema."""

    horizon: int
    feature_columns: list[str]
    classifier: Any
    regressor: Any
    linear_classifier: Any
    linear_regressor: Any

    def as_dict(self) -> dict[str, Any]:
        return {
            "horizon": self.horizon,
            "feature_columns": self.feature_columns,
            "classifier": self.classifier,
            "regressor": self.regressor,
            "linear_classifier": self.linear_classifier,
            "linear_regressor": self.linear_regressor,
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
    """Fit one classifier/regressor pair and the identical-fold linear baselines."""
    mask = np.asarray(train_rows, dtype=bool)
    if len(mask) != len(features):
        raise ValueError("train_rows must align with features")
    direction_column = f"target_h{horizon}_dir"
    magnitude_column = f"target_h{horizon}_mag_bps"
    for column in (direction_column, magnitude_column):
        if column not in targets:
            raise ValueError(f"targets need {column!r}")
    usable = mask & targets[direction_column].notna().to_numpy() & targets[magnitude_column].notna().to_numpy()
    if usable.sum() < 3:
        raise ValueError("training rows do not contain enough valid labels")
    X = features.loc[usable].copy()
    y_direction = pd.Categorical(targets.loc[usable, direction_column], categories=CLASS_ORDER).codes
    y_magnitude = pd.to_numeric(targets.loc[usable, magnitude_column], errors="raise").to_numpy(dtype=float)
    if (y_direction < 0).any():
        raise ValueError("training labels contain an unknown direction class")
    class_weights = _class_weights(y_direction)
    lgb = _lightgbm()
    params = {
        "n_estimators": int(n_estimators), "learning_rate": 0.03, "num_leaves": 31,
        "min_child_samples": 400, "max_bin": 127, "feature_fraction": 0.80,
        "bagging_fraction": 0.80, "bagging_freq": 1, "reg_alpha": 0.5, "reg_lambda": 5.0,
        "random_state": int(seed), "feature_fraction_seed": int(seed), "bagging_seed": int(seed),
        "data_random_seed": int(seed), "deterministic": True, "force_col_wise": True,
        "n_jobs": int(num_threads), "verbosity": -1,
    }
    if len(np.unique(y_direction)) < 2:
        classifier = DummyClassifier(strategy="prior")
        classifier.fit(X, y_direction)
    else:
        classifier = lgb.LGBMClassifier(objective="multiclass", num_class=3, class_weight=class_weights, **params)
        classifier.fit(X, y_direction, categorical_feature=_categorical_columns(X))
    regressor = lgb.LGBMRegressor(objective="huber", alpha=0.90, **params)
    regressor.fit(X, y_magnitude, categorical_feature=_categorical_columns(X))
    preprocessor = _linear_preprocessor(X)
    linear_classifier = Pipeline([
        ("preprocess", preprocessor),
        ("model", _logistic_model(seed) if len(np.unique(y_direction)) > 1 else DummyClassifier(strategy="prior")),
    ])
    linear_classifier.fit(X, y_direction)
    linear_regressor = Pipeline([
        ("preprocess", _linear_preprocessor(X)),
        ("model", Ridge(alpha=10)),
    ])
    linear_regressor.fit(X, y_magnitude)
    return HorizonModels(int(horizon), list(features.columns), classifier, regressor, linear_classifier, linear_regressor)


def predict_horizon(
    models: HorizonModels | dict[str, Any],
    features: pd.DataFrame,
    *,
    close: pd.Series | np.ndarray | None = None,
    linear: bool = False,
) -> pd.DataFrame:
    """Return independent direction probabilities and magnitude predictions."""
    bundle = _bundle(models)
    X = features.reindex(columns=bundle.feature_columns).copy()
    classifier = bundle.linear_classifier if linear else bundle.classifier
    regressor = bundle.linear_regressor if linear else bundle.regressor
    probabilities = np.asarray(classifier.predict_proba(X), dtype=float)
    classes = np.asarray(getattr(classifier, "classes_", np.arange(probabilities.shape[1])), dtype=int)
    full = np.zeros((len(X), 3), dtype=float)
    for index, label in enumerate(classes):
        if 0 <= int(label) < 3:
            full[:, int(label)] = probabilities[:, index]
    labels = np.asarray(CLASS_ORDER)[full.argmax(axis=1)]
    magnitude = np.asarray(regressor.predict(X), dtype=float)
    result = pd.DataFrame({
        "p_down_raw": full[:, 0], "p_flat_raw": full[:, 1], "p_up_raw": full[:, 2],
        "direction": labels, "expected_log_return_bps": magnitude,
    }, index=features.index)
    if close is not None:
        result["predicted_close"] = pd.to_numeric(pd.Series(close, index=features.index), errors="coerce") * np.exp(magnitude / 10_000)
    return result


def _bundle(models: HorizonModels | dict[str, Any]) -> HorizonModels:
    if isinstance(models, HorizonModels):
        return models
    return HorizonModels(**models)


def _categorical_columns(frame: pd.DataFrame) -> list[str]:
    return [column for column in frame.columns if isinstance(frame[column].dtype, pd.CategoricalDtype)]


def _linear_preprocessor(frame: pd.DataFrame) -> ColumnTransformer:
    categorical = [column for column in frame.columns if isinstance(frame[column].dtype, pd.CategoricalDtype)]
    numeric = [column for column in frame.columns if column not in categorical]
    return ColumnTransformer([
        ("numeric", Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]), numeric),
        ("categorical", Pipeline([("impute", SimpleImputer(strategy="most_frequent")), ("onehot", OneHotEncoder(categories=[REGIME_CATEGORIES], handle_unknown="ignore"))]), categorical),
    ], remainder="drop")


def _class_weights(labels: np.ndarray) -> dict[int, float]:
    counts = np.bincount(labels, minlength=3).astype(float)
    present = counts > 0
    weights = np.ones(3, dtype=float)
    weights[present] = len(labels) / (present.sum() * counts[present])
    weights = np.clip(weights, 0.5, 2.0)
    return {index: float(weights[index]) for index in np.flatnonzero(present)}


def _lightgbm() -> Any:
    import lightgbm as lgb
    return lgb


def _logistic_model(seed: int) -> LogisticRegression:
    """Use the frozen multinomial setting where supported by sklearn."""
    try:
        return LogisticRegression(C=1, class_weight="balanced", multi_class="multinomial", max_iter=1000, random_state=seed)
    except TypeError:
        return LogisticRegression(C=1, class_weight="balanced", max_iter=1000, random_state=seed)
