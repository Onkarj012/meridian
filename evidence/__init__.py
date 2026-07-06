"""Evidence engine for full-cost validation and promotion."""

from .backtest import run_backtest
from .gates import DEFAULT_PROMOTION_THRESHOLDS, PromotionGateThresholds, build_promotion_readiness_report
from .stats import block_bootstrap_ci, economic_metrics

__all__ = [
    "DEFAULT_PROMOTION_THRESHOLDS",
    "PromotionGateThresholds",
    "block_bootstrap_ci",
    "build_promotion_readiness_report",
    "economic_metrics",
    "run_backtest",
]
