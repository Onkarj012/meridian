"""Isotonic calibration and confidence-threshold execution helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np
from sklearn.isotonic import IsotonicRegression


@dataclass(frozen=True)
class ThresholdDecision:
    index: int
    probability: float
    confidence: float
    execute: bool
    side: str


class PredictionCalibrator:
    """StockXpert-style per-horizon isotonic calibrator."""

    def __init__(self, horizons: Sequence[int] = (1, 3, 5, 7, 10), min_samples: int = 2):
        self.horizons = tuple(int(item) for item in horizons)
        self.min_samples = int(min_samples)
        self.mag_regressors: dict[int, IsotonicRegression | None] = {}
        self.prob_regressors: dict[int, IsotonicRegression | None] = {}
        self.fitted = False

    def fit(
        self,
        mag_preds: np.ndarray,
        mag_targets: np.ndarray,
        prob_preds: np.ndarray,
        dir_targets: np.ndarray,
    ) -> "PredictionCalibrator":
        mag_preds = _as_2d(mag_preds)
        mag_targets = _as_2d(mag_targets)
        prob_preds = _as_2d(prob_preds)
        dir_targets = _as_2d(dir_targets)
        for i, horizon in enumerate(self.horizons):
            x_mag, y_mag = _valid_pair(mag_preds[:, i], mag_targets[:, i])
            if len(x_mag) >= self.min_samples and len(set(x_mag.tolist())) > 1:
                reg = IsotonicRegression(out_of_bounds="clip", increasing=True)
                reg.fit(x_mag, y_mag)
                self.mag_regressors[horizon] = reg
            else:
                self.mag_regressors[horizon] = None

            x_prob, y_dir = _valid_pair(prob_preds[:, i], dir_targets[:, i])
            if len(x_prob) >= self.min_samples and len(set(x_prob.tolist())) > 1:
                reg_prob = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0, increasing=True)
                reg_prob.fit(x_prob, y_dir)
                self.prob_regressors[horizon] = reg_prob
            else:
                self.prob_regressors[horizon] = None
        self.fitted = True
        return self

    def calibrate(self, mag_preds: np.ndarray, prob_preds: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        mag_preds = _as_2d(mag_preds)
        prob_preds = _as_2d(prob_preds)
        if not self.fitted:
            return mag_preds * 0.01, prob_preds

        cal_returns = np.zeros_like(mag_preds, dtype=float)
        cal_probs = np.zeros_like(prob_preds, dtype=float)
        for i, horizon in enumerate(self.horizons):
            mag_reg = self.mag_regressors.get(horizon)
            prob_reg = self.prob_regressors.get(horizon)
            cal_returns[:, i] = mag_reg.predict(mag_preds[:, i]) if mag_reg is not None else mag_preds[:, i] * 0.01
            cal_probs[:, i] = prob_reg.predict(prob_preds[:, i]) if prob_reg is not None else prob_preds[:, i]
        return cal_returns, np.clip(cal_probs, 0.0, 1.0)


def fit_isotonic_calibrator(probabilities: Iterable[float], labels: Iterable[float]) -> IsotonicRegression:
    """Fit a single probability calibrator for lightweight callers/tests."""
    x = np.asarray(list(probabilities), dtype=float)
    y = np.asarray(list(labels), dtype=float)
    if len(x) != len(y):
        raise ValueError("probabilities and labels must have the same length")
    if len(x) < 2:
        raise ValueError("at least two calibration samples are required")
    return IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0, increasing=True).fit(x, y)


def confidence_threshold_decisions(
    probabilities: Iterable[float],
    *,
    threshold: float,
    long_cutoff: float = 0.5,
) -> list[ThresholdDecision]:
    """Convert calibrated probabilities into a selective execution interface."""
    decisions: list[ThresholdDecision] = []
    for index, probability in enumerate(float(item) for item in probabilities):
        side = "LONG" if probability >= long_cutoff else "SHORT"
        confidence = abs(probability - 0.5) * 2.0
        decisions.append(
            ThresholdDecision(
                index=index,
                probability=probability,
                confidence=confidence,
                execute=confidence >= threshold,
                side=side,
            )
        )
    return decisions


def _as_2d(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if array.ndim == 1:
        array = array.reshape(-1, 1)
    if array.ndim != 2:
        raise ValueError("calibration arrays must be one- or two-dimensional")
    return array


def _valid_pair(x_values: np.ndarray, y_values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mask = ~np.isnan(x_values) & ~np.isnan(y_values)
    return x_values[mask], y_values[mask]
