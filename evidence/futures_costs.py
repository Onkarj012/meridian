"""Futures transaction cost helpers for Sleeve F evidence."""
from __future__ import annotations

import math

COST_SCENARIOS_BPS = [2.0, 3.0, 5.0, 8.0]
DEFAULT_PRODUCTION_FUTURES_COST_BPS = 3.5


def futures_round_trip_cost_bps(
    notional_inr: float,
    *,
    brokerage_per_order_inr: float = 20.0,
    stt_sell_bps: float = 2.0,
    exchange_txn_charge_bps: float = 0.173,
    gst_rate: float = 0.18,
    sebi_bps: float = 0.01,
    stamp_buy_bps: float = 0.2,
    slippage_bps: float = 1.0,
) -> float:
    """Return a round-trip futures cost in bps of one-side notional."""
    notional = float(notional_inr)
    if not math.isfinite(notional) or notional <= 0:
        raise ValueError("notional_inr must be positive")

    brokerage = 2.0 * float(brokerage_per_order_inr)
    exchange_txn = _bps_to_inr(notional, 2.0 * float(exchange_txn_charge_bps))
    gst = float(gst_rate) * (brokerage + exchange_txn)
    cash_cost = (
        brokerage
        + _bps_to_inr(notional, float(stt_sell_bps))
        + exchange_txn
        + gst
        + _bps_to_inr(notional, 2.0 * float(sebi_bps))
        + _bps_to_inr(notional, float(stamp_buy_bps))
    )
    cash_cost_bps = cash_cost / notional * 10000.0
    return cash_cost_bps + 2.0 * float(slippage_bps)


def _bps_to_inr(notional_inr: float, bps: float) -> float:
    return float(notional_inr) * float(bps) / 10000.0
