from __future__ import annotations

from typing import Any

from evidence.costs import DEFAULT_DELIVERY_ROUND_TRIP_BPS, calculate_round_trip_cost
from evidence.stats import economic_metrics


FULL_COST_BPS = DEFAULT_DELIVERY_ROUND_TRIP_BPS
EXECUTION_SCENARIOS = {
    "full_cost": {"fill": "all"},
}


def execution_quality_scenarios(rows: list[dict[str, Any]], sides: list[str]) -> dict[str, dict[str, float]]:
    """Deterministic execution views from preserved gross outcomes.

    These scenarios are not a broker simulator. They make the current validation
    evidence honest about whether a candidate survives plausible fill/cost
    assumptions, including missed passive fills.
    """
    output: dict[str, dict[str, float]] = {}
    for name, config in EXECUTION_SCENARIOS.items():
        returns = []
        missed = 0
        for row, side in zip(rows, sides):
            if not _fills(row, str(config["fill"])):
                missed += 1
                continue
            gross = float(row.get(f"{side}_gross_return_bps", row.get(f"{side}_return_bps", 0)) or 0)
            notional = float(row.get("notional", 100_000) or 100_000)
            product = str(row.get("product", "delivery") or "delivery").lower()
            liquidity_bucket = str(row.get("liquidity_bucket", "liquid") or "liquid")
            cost = float(calculate_round_trip_cost(notional, product=product, liquidity_bucket=liquidity_bucket)["total_bps"])
            returns.append(gross - cost)
        economics = economic_metrics(returns)
        output[name] = {
            "cost_bps": FULL_COST_BPS,
            "attempted_trades": len(rows),
            "filled_trades": len(returns),
            "missed_trades": missed,
            "fill_rate": len(returns) / len(rows) if rows else 0.0,
            "hit_rate": sum(value > 0 for value in returns) / len(returns) if returns else 0.0,
            "expected_value_bps": sum(returns) / len(returns) if returns else 0.0,
            "profit_factor": float(economics.get("profit_factor", 0.0)),
            "expectancy_ci95_low_bps": float(economics.get("expectancy_ci95_low_bps", 0.0)),
            "max_drawdown_bps": float(economics.get("max_drawdown_bps", 0.0)),
        }
    return output


def _fills(row: dict[str, Any], policy: str) -> bool:
    if policy == "all":
        return True
    volume_ratio = float(row.get("volume_ratio", 1.0) or 1.0)
    atr_bps = float(row.get("atr_bps", row.get("atr", 0.0)) or 0.0)
    if policy == "liquid":
        return volume_ratio >= 0.75
    if policy == "liquid_low_vol":
        return volume_ratio >= 1.0 and (atr_bps <= 180.0 or atr_bps == 0.0)
    raise ValueError(f"unknown execution fill policy: {policy}")
