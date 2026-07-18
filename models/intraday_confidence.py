"""Session-weighted top-label correctness calibration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression


@dataclass
class CorrectnessCalibrator:
    """Frozen calibration method and fitted estimator."""

    method: str
    estimator: Any
    raw_confidence_values: int
    sessions: int
    rows: int
    correct: int
    incorrect: int


def fit_correctness_calibrator(
    probabilities: pd.DataFrame | np.ndarray,
    y_true: pd.Series | np.ndarray,
    sessions: pd.Series | np.ndarray,
    *,
    min_sessions: int = 20,
    min_rows: int = 2_000,
    min_correct: int = 200,
    min_incorrect: int = 200,
    min_distinct_confidences: int = 20,
) -> CorrectnessCalibrator:
    """Fit isotonic calibration or the specified Platt fallback.

    Rows in each session receive total weight one, preventing long sessions
    from dominating the calibration curve.
    """
    matrix = _probabilities(probabilities)
    truth = _truth(y_true)
    session_values = pd.Series(sessions).astype(str).to_numpy()
    if not (len(matrix) == len(truth) == len(session_values)):
        raise ValueError("probabilities, y_true, and sessions must align")
    confidence = matrix.max(axis=1)
    correctness = (matrix.argmax(axis=1) == truth).astype(int)
    counts = pd.Series(session_values).value_counts()
    weights = np.array([1.0 / counts[value] for value in session_values], dtype=float)
    enough = (
        len(counts) >= min_sessions and len(confidence) >= min_rows
        and int(correctness.sum()) >= min_correct and int((1 - correctness).sum()) >= min_incorrect
        and int(np.unique(confidence).size) >= min_distinct_confidences
    )
    if enough:
        estimator = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        estimator.fit(confidence, correctness, sample_weight=weights)
        method = "isotonic"
    else:
        estimator = _fit_platt(confidence, correctness, weights)
        method = "platt"
    return CorrectnessCalibrator(method, estimator, int(np.unique(confidence).size), len(counts), len(confidence), int(correctness.sum()), int((1 - correctness).sum()))


def calibrate_confidence(
    calibrator: CorrectnessCalibrator | dict[str, Any],
    probabilities: pd.DataFrame | np.ndarray,
) -> np.ndarray:
    """Apply a frozen calibrator to raw probability vectors."""
    matrix = _probabilities(probabilities)
    raw = matrix.max(axis=1)
    cal = calibrator if isinstance(calibrator, CorrectnessCalibrator) else CorrectnessCalibrator(**calibrator)
    if cal.method == "isotonic":
        return np.asarray(cal.estimator.predict(raw), dtype=float)
    return np.asarray(cal.estimator.predict_proba(_logit(raw).reshape(-1, 1))[:, 1], dtype=float)


def correctness_labels(probabilities: pd.DataFrame | np.ndarray, y_true: pd.Series | np.ndarray) -> np.ndarray:
    """Return the binary top-label correctness target used by calibration."""
    matrix = _probabilities(probabilities)
    return (matrix.argmax(axis=1) == _truth(y_true)).astype(int)


def _fit_platt(confidence: np.ndarray, correctness: np.ndarray, weights: np.ndarray) -> Any:
    if np.unique(correctness).size < 2:
        return _ConstantProbability(float(correctness.mean()))
    model = LogisticRegression(C=1.0, solver="lbfgs", max_iter=1000)
    model.fit(_logit(confidence).reshape(-1, 1), correctness, sample_weight=weights)
    return model


class _ConstantProbability:
    def __init__(self, value: float) -> None:
        self.value = value

    def predict_proba(self, values: np.ndarray) -> np.ndarray:
        p = np.full(len(values), self.value, dtype=float)
        return np.column_stack([1 - p, p])


def _probabilities(value: pd.DataFrame | np.ndarray) -> np.ndarray:
    if isinstance(value, pd.DataFrame):
        columns = ["p_down_raw", "p_flat_raw", "p_up_raw"]
        if set(columns).issubset(value.columns):
            value = value[columns]
    matrix = np.asarray(value, dtype=float)
    if matrix.ndim != 2 or matrix.shape[1] != 3:
        raise ValueError("probabilities must have three class columns")
    return matrix


def _truth(value: pd.Series | np.ndarray) -> np.ndarray:
    if isinstance(value, pd.Series) and not pd.api.types.is_numeric_dtype(value):
        return pd.Categorical(value, categories=("DOWN", "FLAT", "UP")).codes
    array = np.asarray(value)
    if array.dtype.kind in "OUS":
        return pd.Categorical(array, categories=("DOWN", "FLAT", "UP")).codes
    return array.astype(int)


def _logit(value: np.ndarray) -> np.ndarray:
    clipped = np.clip(value, 1e-6, 1 - 1e-6)
    return np.log(clipped / (1 - clipped))
