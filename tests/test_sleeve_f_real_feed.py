import pytest

from evidence.sleeve_f_real import build_front_month_replay_trades, evaluate_replay_trades, run_sleeve_f_real_feed_evidence, summarize_replay_candidate
from evidence.futures_costs import COST_SCENARIOS_BPS, futures_round_trip_cost_bps
from evidence.sleeves import DEFAULT_PROMOTION_THRESHOLDS, FUTURES_PROMOTION_THRESHOLDS, futures_promotion_gates_from_metrics
from evidence.stats import DAY_BLOCK_BOOTSTRAP_CONFIDENCE, SLEEVE_F_CAMPAIGN_TRIALS, day_block_bootstrap_ci, day_block_bootstrap_ci_label
from features.sleeve_f import validate_real_futures_feed
from features.sleeve_f_signals import breakdown_n, breakout_n, momentum_n, realized_vol_n, session_filter, vwap_deviation


def test_nifty_continuous_futures_rows_pass_and_index_proxy_rows_fail() -> None:
    futures = [
        {
            "date": "2026-01-01",
            "time": f"09:{15 + index:02d}:00",
            "symbol": "NIFTY-I",
            "instrument_type": "FUTIDX",
            "open": 25000 + index,
            "high": 25010 + index,
            "low": 24990 + index,
            "close": 25001 + index,
            "volume": 100 + index,
            "oi": 1000 + index,
        }
        for index in range(3)
    ]
    accepted = validate_real_futures_feed(futures)

    assert accepted["validated"] is True
    assert accepted["failures"] == []

    proxy = [
        {
            "date": "2026-01-01",
            "time": "09:15:00",
            "symbol": "NIFTY 50",
            "instrument_type": "FUTIDX",
            "close": 25000,
            "volume": 100,
            "oi": 1000,
        }
    ]
    rejected = validate_real_futures_feed(proxy)

    assert rejected["validated"] is False
    assert "index_proxy_or_missing_futures_symbol" in rejected["failures"][0]["reasons"]


def test_zero_oi_futures_rows_pass_with_quality_warning() -> None:
    rows = [
        {
            "date": "2026-01-01",
            "time": "09:15:00",
            "symbol": "NIFTY-I",
            "instrument_type": "FUTIDX",
            "open": 25000,
            "high": 25010,
            "low": 24990,
            "close": 25001,
            "volume": 100,
            "oi": 0,
        },
        {
            "date": "2026-01-01",
            "time": "09:16:00",
            "symbol": "NIFTY-I",
            "instrument_type": "FUTIDX",
            "open": 25001,
            "high": 25011,
            "low": 24991,
            "close": 25002,
            "volume": 101,
            "oi": 1001,
        },
    ]

    accepted = validate_real_futures_feed(rows)

    assert accepted["validated"] is True
    assert accepted["failures"] == []
    assert accepted["zero_oi_count"] == 1
    assert accepted["zero_oi_share"] == pytest.approx(0.5)
    assert accepted["warnings"][0]["reasons"] == ["zero_oi"]


def test_zero_volume_futures_rows_pass_with_quality_warning() -> None:
    rows = [
        {
            "date": "2026-01-01",
            "time": "09:15:00",
            "symbol": "NIFTY-I",
            "instrument_type": "FUTIDX",
            "open": 25000,
            "high": 25010,
            "low": 24990,
            "close": 25001,
            "volume": 0,
            "oi": 1000,
        },
        {
            "date": "2026-01-01",
            "time": "09:16:00",
            "symbol": "NIFTY-I",
            "instrument_type": "FUTIDX",
            "open": 25001,
            "high": 25011,
            "low": 24991,
            "close": 25002,
            "volume": 101,
            "oi": 1001,
        },
    ]

    accepted = validate_real_futures_feed(rows)

    assert accepted["validated"] is True
    assert accepted["failures"] == []
    assert accepted["zero_volume_count"] == 1
    assert accepted["zero_volume_share"] == pytest.approx(0.5)
    assert accepted["warnings"][0]["reasons"] == ["zero_volume"]


def test_replay_same_bar_target_and_stop_resolves_to_stop() -> None:
    rows = [
        {"date": "2026-01-01", "time": "09:15:00", "symbol": "NIFTY-I", "open": 100, "high": 100, "low": 100, "close": 100},
        {"date": "2026-01-01", "time": "09:16:00", "symbol": "NIFTY-I", "open": 100, "high": 101, "low": 99.5, "close": 100.4},
    ]

    trades = build_front_month_replay_trades(rows, target_bps=100, stop_bps=50, horizon_bars=1, skip_first_minutes=0)

    assert len(trades) == 1
    assert trades[0]["outcome"] == "stop"
    assert trades[0]["touch_bar"] == 1
    assert trades[0]["gross_bps"] == pytest.approx(-50.0)


def test_replay_skips_entry_when_next_bar_open_is_missing() -> None:
    rows = [
        {"date": "2026-01-01", "time": "09:15:00", "symbol": "NIFTY-I", "open": 100, "high": 100, "low": 100, "close": 100},
        {"date": "2026-01-01", "time": "09:16:00", "symbol": "NIFTY-I", "open": 0, "high": 101, "low": 99, "close": 100.5},
    ]
    diagnostics: dict[str, int] = {}

    trades = build_front_month_replay_trades(rows, target_bps=50, stop_bps=50, horizon_bars=1, skip_first_minutes=0, diagnostics=diagnostics)

    assert trades == []
    assert diagnostics["skipped_missing_entry_open"] == 1


def test_replay_skips_entry_when_next_bar_volume_is_zero_and_surfaces_counter() -> None:
    rows = [
        {"date": "2026-01-01", "time": "09:15:00", "symbol": "NIFTY-I", "open": 100, "high": 100, "low": 100, "close": 100, "volume": 100},
        {"date": "2026-01-01", "time": "09:16:00", "symbol": "NIFTY-I", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 0},
    ]
    diagnostics: dict[str, int] = {}

    trades = build_front_month_replay_trades(rows, target_bps=50, stop_bps=50, horizon_bars=1, skip_first_minutes=0, diagnostics=diagnostics)
    metrics = evaluate_replay_trades(trades, cost_bps=0, replay_diagnostics=diagnostics)
    summary = summarize_replay_candidate(
        trades,
        {"id": "zero-volume-entry", "target_bps": 50, "stop_bps": 50, "horizon_bars": 1},
        cost_bps=0,
        cost_scenarios_bps=[],
        candidate_trials=SLEEVE_F_CAMPAIGN_TRIALS,
        replay_diagnostics=diagnostics,
    )

    assert trades == []
    assert diagnostics["skipped_zero_volume_entry"] == 1
    assert metrics["skipped_zero_volume_entry"] == 1
    assert summary["skipped_zero_volume_entry"] == 1
    assert summary["metrics"]["skipped_zero_volume_entry"] == 1


def test_replay_does_not_enter_without_full_same_day_horizon() -> None:
    rows = [
        {"date": "2026-01-01", "time": "15:28:00", "symbol": "NIFTY-I", "open": 100, "high": 100, "low": 100, "close": 100},
        {"date": "2026-01-01", "time": "15:29:00", "symbol": "NIFTY-I", "open": 100, "high": 100.2, "low": 99.9, "close": 100.2},
        {"date": "2026-01-01", "time": "15:30:00", "symbol": "NIFTY-I", "open": 100.2, "high": 100.4, "low": 100.0, "close": 100.4},
        {"date": "2026-01-02", "time": "09:15:00", "symbol": "NIFTY-I", "open": 100.4, "high": 120.0, "low": 100.4, "close": 120.0},
    ]

    trades = build_front_month_replay_trades(rows, target_bps=500, stop_bps=500, horizon_bars=5, skip_first_minutes=0)

    assert trades == []


def test_replay_non_overlap_suppresses_signals_until_exit() -> None:
    rows = [
        {"date": "2026-01-01", "time": f"09:{15 + index:02d}:00", "symbol": "NIFTY-I", "open": 100, "high": 100.1, "low": 99.9, "close": 100}
        for index in range(6)
    ]

    overlapping = build_front_month_replay_trades(rows, target_bps=500, stop_bps=500, horizon_bars=2, allow_overlap=True, skip_first_minutes=0)
    non_overlapping = build_front_month_replay_trades(rows, target_bps=500, stop_bps=500, horizon_bars=2, skip_first_minutes=0)

    assert len(overlapping) == 4
    assert len(non_overlapping) == 2
    assert [trade["signal_timestamp"] for trade in non_overlapping] == ["2026-01-01T09:15:00", "2026-01-01T09:18:00"]


def test_signal_functions_are_trailing_only() -> None:
    rows = [
        {"date": "2026-01-01", "time": "09:29:00", "symbol": "NIFTY-I", "high": 100, "close": 100, "volume": 10},
        {"date": "2026-01-01", "time": "09:30:00", "symbol": "NIFTY-I", "high": 100, "close": 100, "volume": 10},
        {"date": "2026-01-01", "time": "09:31:00", "symbol": "NIFTY-I", "high": 100, "close": 100, "volume": 10},
        {"date": "2026-01-01", "time": "09:32:00", "symbol": "NIFTY-I", "high": 100, "close": 110, "volume": 10},
    ]

    assert momentum_n(rows, 2)[:3] == [0.0, 0.0, 0.0]
    assert momentum_n(rows, 2)[3] > 0.0
    assert breakout_n(rows, 2) == [False, False, False, True]
    assert breakdown_n([{**row, "low": row["high"], "close": row["close"]} for row in rows], 2) == [False, False, False, False]
    assert vwap_deviation(rows)[1] == pytest.approx(0.0)
    assert vwap_deviation(rows)[3] > 0.0
    assert realized_vol_n(rows, 2)[1] == pytest.approx(0.0)
    assert realized_vol_n(rows, 2)[3] > 0.0
    assert session_filter(rows, session_start_offset=15) == [False, True, True, True]


def test_metric_decomposition_counts_sum_to_trades() -> None:
    trades = [
        {"signal_timestamp": "2026-01-01T09:15:00", "symbol": "NIFTY-I", "outcome": "target", "gross_bps": 20.0},
        {"signal_timestamp": "2026-01-01T09:16:00", "symbol": "NIFTY-I", "outcome": "stop", "gross_bps": -10.0},
        {"signal_timestamp": "2026-01-01T09:17:00", "symbol": "NIFTY-I", "outcome": "timeout", "gross_bps": 5.0},
        {"signal_timestamp": "2026-01-01T09:18:00", "symbol": "NIFTY-I", "outcome": "eod", "gross_bps": 1.0},
    ]

    metrics = evaluate_replay_trades(trades, cost_bps=8)

    assert sum(metrics["outcome_counts"].values()) == metrics["trades"]
    assert metrics["touch_rate"] == pytest.approx(0.25)
    assert metrics["net_win_rate"] == metrics["hit_rate"]
    assert metrics["gross_ev_bps_by_outcome"]["target"] == pytest.approx(20.0)
    assert metrics["net_ev_bps_by_outcome"]["stop"] == pytest.approx(-18.0)


def test_futures_cost_model_component_math() -> None:
    cost = futures_round_trip_cost_bps(1_000_000)

    assert cost == pytest.approx(5.10028)
    assert COST_SCENARIOS_BPS == [2.0, 3.0, 5.0, 8.0]


def test_cost_scenarios_are_reported_on_candidate_summary() -> None:
    rows = _fixture_rows()
    grid = [{"id": "small", "target_bps": 50, "stop_bps": 50, "horizon_bars": 1, "skip_first_minutes": 0}]

    report = run_sleeve_f_real_feed_evidence(rows, grid=grid, grid_committed=True, fixture_mode=True, cost_bps=3.5)

    scenarios = report["candidate"]["cost_scenarios"]
    assert sorted(scenarios) == ["2.0", "3.0", "5.0", "8.0"]
    assert scenarios["5.0"]["net_ev_bps"] == pytest.approx(report["candidate"]["gross_ev_bps"] - 5.0)
    assert "ci_low_bps" in scenarios["5.0"]
    assert "worst_fold_bps" in scenarios["5.0"]
    assert report["stress_survives_5bps"] == report["candidate"]["stress_survives_5bps"]


def test_short_replay_same_bar_target_and_stop_resolves_to_stop() -> None:
    rows = [
        {"date": "2026-01-01", "time": "09:15:00", "symbol": "NIFTY-I", "open": 100, "high": 100, "low": 100, "close": 100},
        {"date": "2026-01-01", "time": "09:16:00", "symbol": "NIFTY-I", "open": 100, "high": 100.6, "low": 99.0, "close": 99.5},
    ]

    trades = build_front_month_replay_trades(
        rows,
        target_bps=100,
        stop_bps=50,
        horizon_bars=1,
        signal_config={"signal": "negative_momentum", "direction": "short", "lookback": 1, "threshold_bps": 0},
        skip_first_minutes=0,
    )

    assert len(trades) == 1
    assert trades[0]["side"] == "short"
    assert trades[0]["outcome"] == "stop"
    assert trades[0]["gross_bps"] == pytest.approx(-50.0)


def test_skip_first_minutes_defaults_to_first_fifteen_minutes() -> None:
    rows = [
        {"date": "2026-01-01", "time": f"09:{15 + index:02d}:00", "symbol": "NIFTY-I", "open": 100, "high": 101, "low": 99, "close": 100}
        for index in range(17)
    ]

    trades = build_front_month_replay_trades(rows, target_bps=200, stop_bps=200, horizon_bars=1, allow_overlap=True)

    assert trades
    assert trades[0]["entry_timestamp"] == "2026-01-01T09:30:00"
    assert all(str(trade["entry_timestamp"]) >= "2026-01-01T09:30:00" for trade in trades)


def test_futures_gate_profile_drops_symbol_requirements() -> None:
    gates = futures_promotion_gates_from_metrics(
        trades=100,
        trading_days=60,
        positive_fold_share=0.6,
        worst_fold_bps=0.0,
        ci_low_bps=0.01,
        dsr=0.01,
        sealed_test_passed=True,
        fixture_mode=False,
    )

    assert all(gates.values())
    assert "symbol_concentration" not in gates
    assert "min_distinct_symbols" not in gates


def test_day_block_bootstrap_ci_is_deterministic_and_conservative() -> None:
    values_by_day = {
        "2026-01-01": [60.0, 70.0],
        "2026-01-02": [55.0],
        "2026-01-03": [-300.0, -280.0],
        "2026-01-04": [80.0],
    }
    flat = [value for values in values_by_day.values() for value in values]
    naive_mean = sum(flat) / len(flat)

    low_one, high_one = day_block_bootstrap_ci(values_by_day, samples=1_000, confidence=DAY_BLOCK_BOOTSTRAP_CONFIDENCE, seed=123)
    low_two, high_two = day_block_bootstrap_ci(values_by_day, samples=1_000, confidence=DAY_BLOCK_BOOTSTRAP_CONFIDENCE, seed=123)

    assert (low_one, high_one) == pytest.approx((low_two, high_two))
    assert min(flat) <= low_one <= high_one <= max(flat)
    assert low_one < naive_mean


def test_replay_summary_uses_day_block_bootstrap_ci_and_wires_dsr_to_gate() -> None:
    trades = [
        {
            "signal_timestamp": f"2026-01-{(index % 8) + 1:02d}T09:{15 + index:02d}:00",
            "symbol": "NIFTY-I",
            "outcome": "timeout",
            "gross_bps": [8.0, 10.0, 12.0, 9.0, 11.0, 7.0, 13.0, 10.0][index % 8],
        }
        for index in range(40)
    ]
    config = {"id": "dsr", "target_bps": 50, "stop_bps": 50, "horizon_bars": 1}

    summary = summarize_replay_candidate(
        trades,
        config,
        cost_bps=3.5,
        cost_scenarios_bps=[5.0],
        candidate_trials=SLEEVE_F_CAMPAIGN_TRIALS,
    )
    expected_ci_low, _ = day_block_bootstrap_ci(
        {
            day: [float(trade["gross_bps"]) - 3.5 for trade in trades if str(trade["signal_timestamp"])[:10] == day]
            for day in sorted({str(trade["signal_timestamp"])[:10] for trade in trades})
        },
        samples=1_000,
        confidence=DAY_BLOCK_BOOTSTRAP_CONFIDENCE,
        seed=42,
    )

    assert summary["ci_low_bps"] == pytest.approx(expected_ci_low)
    assert summary["worst_fold_bps"] != summary["ci_low_bps"]
    assert summary["ci_confidence"] == DAY_BLOCK_BOOTSTRAP_CONFIDENCE
    assert summary["ci_label"] == day_block_bootstrap_ci_label(DAY_BLOCK_BOOTSTRAP_CONFIDENCE)
    assert summary["dsr_trials"] == SLEEVE_F_CAMPAIGN_TRIALS
    assert summary["dsr"] > 0

    gates = futures_promotion_gates_from_metrics(
        trades=summary["trades"],
        trading_days=60,
        positive_fold_share=0.6,
        worst_fold_bps=summary["worst_fold_bps"],
        ci_low_bps=summary["ci_low_bps"],
        dsr=summary["dsr"],
        sealed_test_passed=True,
        fixture_mode=False,
    )
    zero_dsr_gates = futures_promotion_gates_from_metrics(
        trades=summary["trades"],
        trading_days=60,
        positive_fold_share=0.6,
        worst_fold_bps=summary["worst_fold_bps"],
        ci_low_bps=summary["ci_low_bps"],
        dsr=0.0,
        sealed_test_passed=True,
        fixture_mode=False,
    )

    assert gates["deflated_sharpe_positive"] is True
    assert zero_dsr_gates["deflated_sharpe_positive"] is False


def test_sealed_test_uses_futures_threshold_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(DEFAULT_PROMOTION_THRESHOLDS, "worst_fold_floor_bps", 999.0)
    monkeypatch.setattr(FUTURES_PROMOTION_THRESHOLDS, "worst_fold_floor_bps", 0.0)
    rows = []
    for day_index in range(5):
        day = f"2026-01-{day_index + 1:02d}"
        rows.extend(
            [
                {
                    "date": day,
                    "time": "09:15:00",
                    "symbol": "NIFTY-I",
                    "instrument_type": "FUTIDX",
                    "open": 100.0,
                    "high": 100.0,
                    "low": 100.0,
                    "close": 100.0,
                    "volume": 100 + day_index,
                    "oi": 1000 + day_index * 2,
                },
                {
                    "date": day,
                    "time": "09:16:00",
                    "symbol": "NIFTY-I",
                    "instrument_type": "FUTIDX",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 100.0,
                    "close": 100.5,
                    "volume": 110 + day_index,
                    "oi": 1001 + day_index * 2,
                },
            ]
        )

    report = run_sleeve_f_real_feed_evidence(
        rows,
        grid=[{"id": "small", "target_bps": 50, "stop_bps": 50, "horizon_bars": 1, "skip_first_minutes": 0}],
        grid_committed=True,
        fixture_mode=True,
        cost_bps=0,
    )

    assert report["sealed_test"]["passed"] is True
    assert report["holdout_touch_count"] == 3


def test_sealed_split_does_not_influence_config_selection() -> None:
    rows = []
    oi = 1000
    for day_index, day in enumerate(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"]):
        high = 100.5 if day_index < 4 else 102.0
        for minute, close in [("09:15:00", 100.0), ("09:16:00", 100.0)]:
            rows.append(
                {
                    "date": day,
                    "time": minute,
                    "symbol": "NIFTY-I",
                    "instrument_type": "FUTIDX",
                    "open": 100.0,
                    "high": high,
                    "low": 99.6,
                    "close": close,
                    "volume": 100 + oi,
                    "oi": oi,
                }
            )
            oi += 1
    grid = [
        {"id": "validation_winner", "target_bps": 50, "stop_bps": 100, "horizon_bars": 1, "skip_first_minutes": 0},
        {"id": "sealed_winner", "target_bps": 200, "stop_bps": 50, "horizon_bars": 1, "skip_first_minutes": 0},
    ]

    report = run_sleeve_f_real_feed_evidence(rows, grid=grid, grid_committed=True, fixture_mode=True, cost_bps=0)

    assert report["candidate"]["config_id"] == "validation_winner"
    assert report["sealed_test"]["config_id"] == "validation_winner"
    assert report["walkforward_split"]["sealed_test_start"] == "2026-01-05"
    assert "sealed_test" not in report["config_summaries"][1]


def test_non_fixture_replay_requires_signal_config() -> None:
    with pytest.raises(ValueError, match="signal_config required for non-fixture Sleeve F replay"):
        run_sleeve_f_real_feed_evidence(
            _fixture_rows(),
            grid=[{"id": "small", "target_bps": 50, "stop_bps": 50, "horizon_bars": 1, "skip_first_minutes": 0}],
            grid_committed=True,
            fixture_mode=False,
        )


def test_evidence_runner_refuses_uncommitted_grid() -> None:
    report = run_sleeve_f_real_feed_evidence(
        _fixture_rows(),
        grid=[{"id": "small", "target_bps": 50, "stop_bps": 50, "horizon_bars": 1}],
        grid_committed=False,
        fixture_mode=True,
    )

    assert report["status"] == "quarantined"
    assert report["reason"] == "walkforward_grid_not_committed"


def test_evidence_runner_fixture_rows_quarantines_with_metrics() -> None:
    report = run_sleeve_f_real_feed_evidence(
        _fixture_rows(),
        grid=[{"id": "small", "target_bps": 50, "stop_bps": 50, "horizon_bars": 1, "skip_first_minutes": 0}],
        grid_committed=True,
        fixture_mode=True,
        cost_bps=0,
    )

    assert report["status"] == "quarantined"
    assert report["real_feed_validated"] is True
    assert report["metrics"]["trades"] > 0
    assert report["candidate"]["metrics"]["net_ev_bps"] == pytest.approx(report["metrics"]["net_ev_bps"])


def _fixture_rows() -> list[dict[str, object]]:
    return [
        {
            "date": "2026-01-01",
            "time": "09:15:00",
            "symbol": "NIFTY-I",
            "instrument_type": "FUTIDX",
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "volume": 100,
            "oi": 1000,
        },
        {
            "date": "2026-01-01",
            "time": "09:16:00",
            "symbol": "NIFTY-I",
            "instrument_type": "FUTIDX",
            "open": 100.0,
            "high": 101.0,
            "low": 100.0,
            "close": 100.5,
            "volume": 110,
            "oi": 1001,
        },
        {
            "date": "2026-01-01",
            "time": "09:17:00",
            "symbol": "NIFTY-I",
            "instrument_type": "FUTIDX",
            "open": 100.5,
            "high": 100.6,
            "low": 99.0,
            "close": 99.5,
            "volume": 120,
            "oi": 1002,
        },
        {
            "date": "2026-01-01",
            "time": "09:18:00",
            "symbol": "NIFTY-I",
            "instrument_type": "FUTIDX",
            "open": 99.5,
            "high": 100.5,
            "low": 99.4,
            "close": 100.0,
            "volume": 130,
            "oi": 1003,
        },
    ]
