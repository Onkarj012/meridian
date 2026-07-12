"""Date-versioned NSE NIFTY futures round-trip costs.

Tax and exchange fees are selected by *trade date*.  NIFTY lot sizes are a
different kind of date rule: NSE changes them for newly listed contract
generations, so the authoritative ingestion lookup is keyed by expiry month
(``ingest.expired_common.NIFTY_LOT_SIZE_BY_EXPIRY``).  ``ERA_TABLE`` records
the corresponding first fully-applicable trade-date era for cost reporting;
callers that know an expiry must use the ingestion lookup for exact contract
quantity.  These functions cannot infer an expiry from a trade date, and
therefore use the reporting-era lot size for ``cost_rupees``.

All percentage fields retain the percentage notation used by the research
document (for example, ``0.0125`` means 0.0125%), rather than decimal rates.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime
import hashlib
import json
from zoneinfo import ZoneInfo


# The pre-2024 NSE charge was member-turnover slab based.  The research
# document's worked example uses its conservative ₹1.88/lakh end of the
# documented ₹1.73–₹1.88 range; this deterministic engine uses that example
# assumption until the true-to-label flat rate begins.
#
# Source comments deliberately cite the stable local source section rather
# than external pages so that this freeze remains auditable offline.
ERA_TABLE: tuple[dict[str, float | int | str], ...] = (
    {
        # Pre-centralisation stamp duty was state-dependent.  This entry
        # deliberately carries the later national rate as the registered C1
        # proxy, rather than presenting it as a sourced historical rate.
        "effective_date": "2020-01-01",
        "lot_size": 75,  # research §7, lines 81-82 (75 before 2021-07 expiry generation)
        "stt_sell_pct": 0.01,  # research §1, line 17 (pre-2023 baseline)
        "nse_txn_per_lakh_buy": 1.88,  # research §2, line 30 + §7, line 96 (worked-example assumption)
        "nse_txn_per_lakh_sell": 1.88,  # research §2, line 30 + §7, line 96 (per side)
        "sebi_charges": 10.0,  # research §3, line 40 (₹10 per crore turnover)
        "stamp_duty": 0.002,
        "gst_pct": 18.0,  # research §5, line 61 (18% service components)
        "brokerage_per_order": 20.0,  # research §6, line 69 (Zerodha-style ₹20 order class)
        "slippage_bps": 0.0,  # protocol §5, line 99 (headline base schedule; stresses are caller-selected)
        "assumption": (
            "pre-2020-07 stamp duty proxied at national 0.002% "
            "(state-dependent historically); registered assumption, C1 registration §1"
        ),
    },
    {
        "effective_date": "2020-07-01",  # research §4, line 52 (central stamp collection effective)
        "lot_size": 75,  # research §7, lines 81-82 (75 before 2021-07 expiry generation)
        "stt_sell_pct": 0.01,  # research §1, line 17 (pre-2023 baseline)
        "nse_txn_per_lakh_buy": 1.88,  # research §2, line 30 + §7, line 96 (worked-example assumption)
        "nse_txn_per_lakh_sell": 1.88,  # research §2, line 30 + §7, line 96 (per side)
        "sebi_charges": 10.0,  # research §3, line 40 (₹10 per crore turnover)
        "stamp_duty": 0.002,  # research §4, line 52 (0.002%, buy side)
        "gst_pct": 18.0,  # research §5, line 61 (18% service components)
        "brokerage_per_order": 20.0,  # research §6, line 69 (Zerodha-style ₹20 order class)
        "slippage_bps": 0.0,  # protocol §5, line 99 (headline base schedule; stresses are caller-selected)
    },
    {
        "effective_date": "2021-07-01",  # research §7, line 82 (2021-07 expiry onward)
        "lot_size": 50,  # research §7, line 82 (lot reduced from 75)
        "stt_sell_pct": 0.01,  # research §1, line 17 (pre-2023 baseline)
        "nse_txn_per_lakh_buy": 1.88,  # research §2, line 30 + §7, line 96 (conservative slab assumption)
        "nse_txn_per_lakh_sell": 1.88,  # research §2, line 30 + §7, line 96 (per side)
        "sebi_charges": 10.0,  # research §3, line 40 (₹10 per crore)
        "stamp_duty": 0.002,  # research §4, line 52 (0.002% buy side)
        "gst_pct": 18.0,  # research §5, line 61 (18%)
        "brokerage_per_order": 20.0,  # research §6, line 69 (₹20 order class)
        "slippage_bps": 0.0,  # protocol §5, line 99 (base schedule)
    },
    {
        "effective_date": "2023-04-01",  # research §1, line 18 (Finance Act 2023 effective date)
        "lot_size": 50,  # research §7, line 82 (2021-07 expiry onward)
        "stt_sell_pct": 0.0125,  # research §1, line 18 (0.0125% sell side)
        "nse_txn_per_lakh_buy": 1.88,  # research §2, line 30 + §7, line 96 (worked-example assumption)
        "nse_txn_per_lakh_sell": 1.88,  # research §2, line 30 + §7, line 96 (per side)
        "sebi_charges": 10.0,  # research §3, line 40 (₹10 per crore)
        "stamp_duty": 0.002,  # research §4, line 52 (0.002% buy side)
        "gst_pct": 18.0,  # research §5, line 61 (18%)
        "brokerage_per_order": 20.0,  # research §6, line 69 (₹20 order class)
        "slippage_bps": 0.0,  # protocol §5, line 99 (base schedule)
    },
    {
        "effective_date": "2024-07-01",  # ingest expiry table: first 25-lot expiry month; research §7, lines 83-86
        "lot_size": 25,  # research §7, lines 83-86 (new contract generation, retained through 2025-01 expiry)
        "stt_sell_pct": 0.0125,  # research §1, line 18 (pre-2024-10 rate)
        "nse_txn_per_lakh_buy": 1.88,  # research §2, line 30 + §7, line 96 (worked-example assumption)
        "nse_txn_per_lakh_sell": 1.88,  # research §2, line 30 + §7, line 96 (per side)
        "sebi_charges": 10.0,  # research §3, line 40 (₹10 per crore)
        "stamp_duty": 0.002,  # research §4, line 52 (0.002% buy side)
        "gst_pct": 18.0,  # research §5, line 61 (18%)
        "brokerage_per_order": 20.0,  # research §6, line 69 (₹20 order class)
        "slippage_bps": 0.0,  # protocol §5, line 99 (base schedule)
    },
    {
        "effective_date": "2024-10-01",  # research §§1-2, lines 19 and 31 (STT and true-to-label txn change)
        "lot_size": 25,  # research §7, lines 83-86 (25-lot contract generation)
        "stt_sell_pct": 0.02,  # research §1, line 19 (0.02% sell side)
        "nse_txn_per_lakh_buy": 1.73,  # research §2, line 31 (₹1.73/lakh buy side)
        "nse_txn_per_lakh_sell": 1.73,  # research §2, line 31 (₹1.73/lakh sell side)
        "sebi_charges": 10.0,  # research §3, line 40 (₹10 per crore)
        "stamp_duty": 0.002,  # research §4, line 52 (0.002% buy side)
        "gst_pct": 18.0,  # research §5, line 61 (18%)
        "brokerage_per_order": 20.0,  # research §6, line 69 (₹20 order class)
        "slippage_bps": 0.0,  # protocol §5, line 99 (base schedule)
    },
    {
        "effective_date": "2025-02-01",  # ingest expiry table: first 75-lot expiry month; research §7, lines 84-86
        "lot_size": 75,  # research §7, lines 84-86 (2025 expiry generation)
        "stt_sell_pct": 0.02,  # research §1, line 19 (0.02% sell side)
        "nse_txn_per_lakh_buy": 1.73,  # research §2, lines 31-32 (flat rate unchanged)
        "nse_txn_per_lakh_sell": 1.73,  # research §2, lines 31-32 (flat rate unchanged)
        "sebi_charges": 10.0,  # research §3, line 40 (₹10 per crore)
        "stamp_duty": 0.002,  # research §4, line 53 (unchanged)
        "gst_pct": 18.0,  # research §5, line 61 (18%)
        "brokerage_per_order": 20.0,  # research §6, line 69 (₹20 order class)
        "slippage_bps": 0.0,  # protocol §5, line 99 (base schedule)
    },
    {
        "effective_date": "2026-01-01",  # ingest expiry table: 2026+ expiry generation; research §7, lines 85-86
        "lot_size": 65,  # research §7, lines 85-86 (65-lot contract generation)
        "stt_sell_pct": 0.02,  # research §1, line 19 (pre-2026-04 rate)
        "nse_txn_per_lakh_buy": 1.73,  # research §2, lines 31-32 (flat rate unchanged)
        "nse_txn_per_lakh_sell": 1.73,  # research §2, lines 31-32 (flat rate unchanged)
        "sebi_charges": 10.0,  # research §3, line 40 (₹10 per crore)
        "stamp_duty": 0.002,  # research §4, line 53 (unchanged)
        "gst_pct": 18.0,  # research §5, line 61 (18%)
        "brokerage_per_order": 20.0,  # research §6, line 69 (₹20 order class)
        "slippage_bps": 0.0,  # protocol §5, line 99 (base schedule)
    },
    {
        "effective_date": "2026-04-01",  # research §1, line 20 (Finance 2026 STT effective date)
        "lot_size": 65,  # research §7, lines 85-86 (2026 expiry generation)
        "stt_sell_pct": 0.05,  # research §1, line 20 (0.05% sell side)
        "nse_txn_per_lakh_buy": 1.73,  # research §2, lines 31-32 (flat rate unchanged)
        "nse_txn_per_lakh_sell": 1.73,  # research §2, lines 31-32 (flat rate unchanged)
        "sebi_charges": 10.0,  # research §3, line 40 (₹10 per crore)
        "stamp_duty": 0.002,  # research §4, line 53 (unchanged)
        "gst_pct": 18.0,  # research §5, line 61 (18%)
        "brokerage_per_order": 20.0,  # research §6, line 69 (₹20 order class)
        "slippage_bps": 0.0,  # protocol §5, line 99 (base schedule)
    },
)

STRESS_SLIPPAGE_BPS = (3.5, 5.0, 7.0)  # protocol §5, line 99


def _as_date(value: date | str) -> date:
    if isinstance(value, datetime):
        if value.tzinfo is not None and value.utcoffset() is not None:
            return value.astimezone(ZoneInfo("Asia/Kolkata")).date()
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _era_for(trade_date: date | str) -> Mapping[str, float | int | str]:
    day = _as_date(trade_date)
    matching = [era for era in ERA_TABLE if date.fromisoformat(str(era["effective_date"])) <= day]
    if not matching:
        raise ValueError("cost schedule begins on 2020-01-01; earlier stamp-duty rate is not sourced")
    return matching[-1]


def _pct(value: float | int | str) -> float:
    return float(value) / 100.0


def _cost_items(
    era: Mapping[str, float | int | str],
    entry_notional: float,
    exit_notional: float,
    slippage_bps: float,
) -> dict[str, float]:
    brokerage_buy = float(era["brokerage_per_order"])
    brokerage_sell = float(era["brokerage_per_order"])
    nse_txn_buy = entry_notional * float(era["nse_txn_per_lakh_buy"]) / 100_000.0
    nse_txn_sell = exit_notional * float(era["nse_txn_per_lakh_sell"]) / 100_000.0
    sebi_buy = entry_notional * float(era["sebi_charges"]) / 10_000_000.0
    sebi_sell = exit_notional * float(era["sebi_charges"]) / 10_000_000.0
    stamp_duty_buy = entry_notional * _pct(era["stamp_duty"])
    stt_sell = exit_notional * _pct(era["stt_sell_pct"])
    gst = _pct(era["gst_pct"]) * (
        brokerage_buy + brokerage_sell + nse_txn_buy + nse_txn_sell + sebi_buy + sebi_sell
    )
    slippage = ((entry_notional + exit_notional) / 2.0) * slippage_bps / 10_000.0
    items = {
        "brokerage_buy": brokerage_buy,
        "brokerage_sell": brokerage_sell,
        "nse_txn_buy": nse_txn_buy,
        "nse_txn_sell": nse_txn_sell,
        "sebi_buy": sebi_buy,
        "sebi_sell": sebi_sell,
        "stamp_duty_buy": stamp_duty_buy,
        "stt_sell": stt_sell,
        "gst": gst,
        "slippage": slippage,
    }
    items["total"] = sum(items.values())
    return items


def cost_bps(trade_date: date, notional_rupees: float, *, slippage_bps: float | None = None) -> float:
    """Return one-lot buy/sell round-trip friction in basis points of one-leg notional."""
    notional = float(notional_rupees)
    if notional <= 0:
        raise ValueError("notional_rupees must be positive")
    era = _era_for(trade_date)
    slip = float(era["slippage_bps"] if slippage_bps is None else slippage_bps)
    if slip < 0:
        raise ValueError("slippage_bps must be non-negative")
    return _cost_items(era, notional, notional, slip)["total"] / notional * 10_000.0


def cost_rupees(
    trade_date: date,
    entry_price: float,
    exit_price: float,
    *,
    lots: int = 1,
    slippage_bps: float | None = None,
) -> dict[str, float]:
    """Return an auditable round-trip charge breakdown for one submitted order per leg."""
    entry = float(entry_price)
    exit_ = float(exit_price)
    if entry <= 0 or exit_ <= 0:
        raise ValueError("entry_price and exit_price must be positive")
    if isinstance(lots, bool) or int(lots) != lots or lots < 1:
        raise ValueError("lots must be a positive integer")
    era = _era_for(trade_date)
    slip = float(era["slippage_bps"] if slippage_bps is None else slippage_bps)
    if slip < 0:
        raise ValueError("slippage_bps must be non-negative")
    quantity = int(era["lot_size"]) * int(lots)
    items = _cost_items(era, entry * quantity, exit_ * quantity, slip)
    return {
        "lot_size": float(era["lot_size"]),
        "lots": float(lots),
        "quantity": float(quantity),
        "entry_notional": entry * quantity,
        "exit_notional": exit_ * quantity,
        **items,
    }


def era_table_hash() -> str:
    """Return SHA-256 of the canonical, order-preserving schedule artifact."""
    canonical = json.dumps(ERA_TABLE, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def cost_bps_fn_for(
    slippage_bps: float | None = None,
    *,
    reference_notional: float = 1_000_000.0,
) -> Callable[[date], float]:
    """Build the ``Callable[[date], float]`` injection seam used by label generators."""
    if reference_notional <= 0:
        raise ValueError("reference_notional must be positive")
    return lambda trade_date: cost_bps(
        trade_date, reference_notional, slippage_bps=slippage_bps
    )
