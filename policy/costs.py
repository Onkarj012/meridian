"""Policy-facing wrapper for the versioned full Indian cost model."""
from __future__ import annotations

from evidence.costs import DEFAULT_DELIVERY_ROUND_TRIP_BPS, calculate_round_trip_cost


FULL_COST_BPS = DEFAULT_DELIVERY_ROUND_TRIP_BPS


def calculate_full_costs(notional: float = 0.0, **kwargs):
    product = str(kwargs.get("product", "delivery") or "delivery").lower()
    liquidity_bucket = str(kwargs.get("liquidity_bucket", "liquid") or "liquid")
    return calculate_round_trip_cost(float(notional or 0.0), product=product, liquidity_bucket=liquidity_bucket)
