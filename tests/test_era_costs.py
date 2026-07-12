from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from features.c2_sets import derive_monthly_expiries
from ingest.expired_common import nifty_lot_size
from policy.era_costs import (
    ERA_TABLE,
    STRESS_SLIPPAGE_BPS,
    _era_for,
    cost_bps,
    cost_bps_fn_for,
    cost_rupees,
    era_table_hash,
)


def test_october_2024_boundary_changes_stt_and_transaction_charge() -> None:
    before = cost_rupees(date(2024, 9, 30), 24_000, 24_000)
    after = cost_rupees(date(2024, 10, 1), 24_000, 24_000)
    assert before["stt_sell"] == pytest.approx(75.0)
    assert after["stt_sell"] == pytest.approx(120.0)
    assert before["nse_txn_buy"] == pytest.approx(11.28)
    assert after["nse_txn_buy"] == pytest.approx(10.38)


def test_aware_datetime_is_compared_in_ist_calendar():
    assert cost_rupees(datetime(2024, 9, 30, 19, tzinfo=timezone.utc), 24_000, 24_000)["stt_sell"] == pytest.approx(120.0)


def test_january_2026_boundary_changes_reporting_lot_size() -> None:
    assert cost_rupees(date(2025, 12, 31), 25_000, 25_000)["lot_size"] == 75
    assert cost_rupees(date(2026, 1, 1), 25_000, 25_000)["lot_size"] == 65


def test_april_2026_boundary_changes_stt() -> None:
    before = cost_rupees(date(2026, 3, 31), 25_000, 25_000)
    after = cost_rupees(date(2026, 4, 1), 25_000, 25_000)
    assert before["stt_sell"] == pytest.approx(325.0)
    assert after["stt_sell"] == pytest.approx(812.5)


@pytest.mark.parametrize(
    ("trade_date", "notional", "expected_bps"),
    [
        (date(2023, 6, 1), 930_000, 2.4259),
        (date(2024, 11, 1), 607_500, 3.4081),
        (date(2025, 6, 1), 1_856_250, 2.8867),
    ],
)
def test_research_worked_examples_match_documented_round_trip_bps(
    trade_date: date, notional: float, expected_bps: float
) -> None:
    actual = cost_bps(trade_date, notional)
    assert 2.4 <= actual <= 3.5
    assert actual == pytest.approx(expected_bps, abs=0.01)


def test_itemization_sums_to_total_and_slippage_is_explicit() -> None:
    result = cost_rupees(date(2025, 6, 1), 24_750, 24_900, lots=2, slippage_bps=3.5)
    charges = (
        "brokerage_buy", "brokerage_sell", "nse_txn_buy", "nse_txn_sell", "sebi_buy",
        "sebi_sell", "stamp_duty_buy", "stt_sell", "gst", "slippage",
    )
    assert sum(result[charge] for charge in charges) == pytest.approx(result["total"])
    assert result["slippage"] > 0


def test_hash_is_pinned() -> None:
    assert era_table_hash() == "5e1954ce973d8a1806dcd11e51a33220bc05eab614bc0cbb4a40f65de658e74f"


def test_2020_cost_schedule_boundary_uses_registered_stamp_duty_proxy() -> None:
    with pytest.raises(ValueError, match="2020-01-01"):
        cost_bps(date(2019, 12, 31), 1_000_000)

    january = cost_rupees(date(2020, 1, 1), 24_000, 24_000)
    june = cost_rupees(date(2020, 6, 30), 24_000, 24_000)
    july = cost_rupees(date(2020, 7, 1), 24_000, 24_000)

    assert cost_bps(date(2020, 1, 1), 1_000_000) == pytest.approx(2.13928)
    assert ERA_TABLE[0]["assumption"] == (
        "pre-2020-07 stamp duty proxied at national 0.002% "
        "(state-dependent historically); registered assumption, C1 registration §1"
    )
    assert january["total"] == pytest.approx(june["total"])
    assert january["stamp_duty_buy"] == pytest.approx(july["stamp_duty_buy"])
    # Both 2020 eras have the researched 0.01% sell-side STT.  The boundary
    # only records the stamp-duty sourcing change, with no numeric difference.
    assert june["stt_sell"] == pytest.approx(180.0)
    assert july["stt_sell"] == pytest.approx(180.0)
    assert june["total"] == pytest.approx(july["total"])


def test_expiry_generation_lookup_and_trade_date_era_are_reconciled_at_crossover() -> None:
    # The ingest table is keyed by the contract expiry generation: September
    # 2025 is already a 75-lot contract even before its expiry date.  The cost
    # table is keyed by trade date and is also in the 75-lot reporting era by
    # 2025-09-30; neither table is a replacement for the other.
    assert nifty_lot_size("2025-09-25") == 75
    assert nifty_lot_size("2025-10-30") == 75
    assert cost_rupees(date(2025, 9, 30), 25_000, 25_000)["lot_size"] == 75


def test_label_cost_callback_and_stress_levels_are_expressible() -> None:
    callback = cost_bps_fn_for(3.5, reference_notional=1_000_000)
    assert callback(date(2025, 6, 1)) == pytest.approx(cost_bps(date(2025, 6, 1), 1_000_000, slippage_bps=3.5))
    assert STRESS_SLIPPAGE_BPS == (3.5, 5.0, 7.0)


def test_era_table_is_chronological() -> None:
    assert [row["effective_date"] for row in ERA_TABLE] == sorted(row["effective_date"] for row in ERA_TABLE)


def _front_expiry_for(trade_date: date) -> str:
    """Derive the front-month expiry for ``trade_date`` using the registered rule."""
    month_start = trade_date.replace(day=1)
    month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    derived = derive_monthly_expiries(pd.bdate_range(month_start, month_end))
    month = trade_date.strftime("%Y-%m")
    matches = derived.loc[derived["month"] == month, "derived_expiry"]
    assert len(matches) == 1, f"expected one derived expiry for {month}"
    return matches.iloc[0].strftime("%Y-%m-%d")


@pytest.mark.parametrize(
    ("trade_date", "expected_lot_size"),
    [
        # Each trade date is safely inside one cost era.  The front-month expiry
        # in that month belongs to a contract generation whose lot size must
        # match the era's reporting lot_size.
        (date(2020, 3, 15), 75),   # 2020-03 expiry generation -> 75
        (date(2022, 6, 15), 50),   # 2021-07..2024-06 generation -> 50
        (date(2024, 8, 15), 25),   # 2024-07..2025-01 generation -> 25
        (date(2025, 6, 15), 75),   # 2025 generation -> 75
        (date(2026, 5, 15), 65),   # 2026+ generation -> 65
    ],
)
def test_era_lot_size_matches_expiry_generation_lookup(
    trade_date: date, expected_lot_size: int
) -> None:
    era = _era_for(trade_date)
    assert era["lot_size"] == expected_lot_size
    front_expiry = _front_expiry_for(trade_date)
    assert nifty_lot_size(front_expiry) == expected_lot_size
