from __future__ import annotations

from policy.c1_sizing import (
    daily_risk_state,
    is_daily_halted,
    lot_size_for,
    risk_unit,
    size_position,
)


def test_lot_size_uses_era_table_transition_dates() -> None:
    assert [
        lot_size_for(day)
        for day in ("2021-07-01", "2024-07-01", "2025-02-01", "2026-01-01")
    ] == [50, 25, 75, 65]


def test_one_point_five_multiplier_requires_whole_lots_in_every_era() -> None:
    for day, lot_size in (
        ("2021-07-01", 50),
        ("2024-07-01", 25),
        ("2025-02-01", 75),
        ("2026-01-01", 65),
    ):
        fallback = size_position(day, 100.0, base_lots=1, requested_multiplier=1.5)
        realized = size_position(day, 100.0, base_lots=2, requested_multiplier=1.5)

        assert (fallback.lot_size, fallback.lots, fallback.multiplier) == (lot_size, 1, 1.0)
        assert (realized.lot_size, realized.lots, realized.multiplier) == (lot_size, 3, 1.5)


def test_daily_halt_triggers_when_floor_adjusted_losses_cross_minus_five_r() -> None:
    trade_log = [
        {"trade_date": "2025-02-03", "entry_notional": 100_000, "realized_pnl": -50_000},
        {"trade_date": "2025-02-03", "entry_notional": 100_000, "realized_pnl": -120_000},
        {"trade_date": "2025-02-03", "entry_notional": 100_000, "realized_pnl": -90_000},
        {"trade_date": "2025-02-03", "entry_notional": 100_000, "realized_pnl": -90_000},
        {"trade_date": "2025-02-03", "entry_notional": 100_000, "realized_pnl": -90_000},
    ]

    assert risk_unit(100_000) == 300.0
    assert not is_daily_halted(trade_log[:4], "2025-02-03")
    assert is_daily_halted(trade_log, "2025-02-03")


def test_daily_halt_state_is_reconstructed_from_trade_log_after_restart() -> None:
    trade_log = [
        {"trade_date": "2025-02-03", "entry_notional": 100_000, "realized_pnl": -300},
        {"trade_date": "2025-02-03", "entry_notional": 100_000, "realized_pnl": -1_200},
        {"trade_date": "2025-02-03", "entry_notional": 100_000, "realized_pnl": -1_200},
        {"trade_date": "2025-02-03", "entry_notional": 100_000, "realized_pnl": -1_200},
        {"trade_date": "2025-02-03", "entry_notional": 100_000, "realized_pnl": -1_200},
    ]

    restored = daily_risk_state(trade_log, "2025-02-03")

    assert restored.realized_r == -5.0
    assert restored.halted
