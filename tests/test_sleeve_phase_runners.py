import pytest

from features.sleeve_f import validate_real_futures_feed
from features.sleeve_m import build_sleeve_m_barrier_labels, run_sleeve_m_scaled_momentum
from features.sleeve_x import (
    build_sleeve_x_ranking_labels,
    evaluate_top_bottom_net_spread,
    run_sleeve_x_rung0_to_rung2,
)
from models.stockxpert_v3 import StockXpertV3Challenger


def test_real_futures_feed_rejects_proxy_and_accepts_normalized_futures_rows() -> None:
    proxy = [
        {
            "timestamp": "2026-01-01T09:15:00+05:30",
            "symbol": "NIFTY 50",
            "tradingsymbol": "NIFTY 50",
            "instrument_token": 256265,
            "expiry": "2026-01-29",
            "close": 25000,
            "volume": 0,
            "oi": 0,
        }
    ]
    rejected = validate_real_futures_feed(proxy)

    assert rejected["validated"] is False
    assert "index_proxy_or_missing_futures_symbol" in rejected["failures"][0]["reasons"]
    assert "missing_or_zero_oi" in rejected["failures"][0]["reasons"]
    assert "missing_or_zero_volume" in rejected["failures"][0]["reasons"]

    futures = [
        {
            "timestamp": f"2026-01-01T09:{15 + index:02d}:00+05:30",
            "symbol": "NIFTY26JANFUT",
            "tradingsymbol": "NIFTY26JANFUT",
            "instrument_token": 123456 + index,
            "expiry": "2026-01-29",
            "instrument_type": "FUTIDX",
            "close": 25000 + index,
            "volume": 100 + index,
            "oi": 1000 + index,
        }
        for index in range(3)
    ]
    accepted = validate_real_futures_feed(futures)

    assert accepted["validated"] is True
    assert accepted["failures"] == []


def test_sleeve_m_same_bar_stop_wins() -> None:
    rows = [
        {"timestamp": "2026-01-01T09:15:00+05:30", "symbol": "ABC", "close": 100.0, "high": 100.0, "low": 100.0},
        {"timestamp": "2026-01-01T09:16:00+05:30", "symbol": "ABC", "close": 100.0, "high": 102.0, "low": 99.0},
    ]

    labels = build_sleeve_m_barrier_labels(rows, target_bps=100, stop_bps=50, horizon_bars=1, side="long")

    assert labels[0]["barrier_label"] == "stop"
    assert labels[0]["touch_bar"] == 1
    assert labels[0]["outcome_bps"] == -50


def test_sleeve_m_production_gate_rejects_small_universe_unless_fixture_mode() -> None:
    rows = [
        {"timestamp": "2026-01-01T09:15:00+05:30", "symbol": "AAA", "close": 100, "forward_return_bps": 20},
        {"timestamp": "2026-01-01T09:15:00+05:30", "symbol": "BBB", "close": 101, "forward_return_bps": 10},
    ]

    production = run_sleeve_m_scaled_momentum(rows, universe_size=2, fixture_mode=False)
    fixture = run_sleeve_m_scaled_momentum(rows, universe_size=2, fixture_mode=True)

    assert production["status"] == "quarantined"
    assert production["reason"] == "universe_size_below_200"
    assert fixture["status"] == "quarantined"
    assert fixture["prerequisites"]["fixture_mode"] is True
    assert "fixture_mode_not_promotable" in fixture["gates"]


def test_sleeve_x_ranking_labels_are_cross_sectional_and_spread_subtracts_costs() -> None:
    rows = [
        {"date": "2026-01-01", "symbol": "AAA", "close": 100, "score": 3.0},
        {"date": "2026-01-01", "symbol": "BBB", "close": 100, "score": 1.0},
        {"date": "2026-01-01", "symbol": "CCC", "close": 100, "score": 2.0},
        {"date": "2026-01-02", "symbol": "AAA", "close": 110, "score": 1.0},
        {"date": "2026-01-02", "symbol": "BBB", "close": 90, "score": 3.0},
        {"date": "2026-01-02", "symbol": "CCC", "close": 100, "score": 2.0},
    ]

    labels = build_sleeve_x_ranking_labels(rows, horizons=(1,))
    day1 = {row["symbol"]: row for row in labels if row["date"] == "2026-01-01"}

    assert day1["AAA"]["rank_1d"] == 1
    assert day1["CCC"]["rank_1d"] == 2
    assert day1["BBB"]["rank_1d"] == 3

    spread = evaluate_top_bottom_net_spread(labels, top_k=1, full_delivery_cost_bps=5.0)

    assert spread["cross_sections"] == 1
    assert spread["mean_gross_spread_bps"] == pytest.approx(2000.0)
    assert spread["mean_net_spread_bps"] == pytest.approx(1990.0)


def test_v3_challenger_is_blocked_until_prerequisites_pass() -> None:
    report = run_sleeve_x_rung0_to_rung2(
        [{"date": "2026-01-01", "symbol": "AAA", "close": 100}],
        pit_ready=False,
        grid_committed=True,
        fixture_mode=True,
        enable_v3_challenger=True,
    )

    assert report["status"] == "quarantined"
    assert report["v3_challenger"]["status"] == "blocked"
    assert report["v3_challenger"]["reason"] == "rung0_to_rung2_prerequisites_failed"

    challenger = StockXpertV3Challenger()
    assert challenger.report()["reason"] == "challenger_not_implemented"
    with pytest.raises(NotImplementedError, match="challenger_not_implemented"):
        challenger.predict([{"symbol": "AAA"}])
