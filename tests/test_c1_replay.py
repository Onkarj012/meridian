"""Focused golden tests for the registered C1 replay state machine."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from evidence.c1_replay import ReplayConfig, replay


FIXTURE = Path(__file__).parent / "fixtures/c1_replay_ambiguities.json"
RULES = json.loads(FIXTURE.read_text(encoding="utf-8"))


def _bars(day: str, times: list[str], *, score_at: dict[str, float] | None = None, prices: dict[str, tuple[float, float, float, float]] | None = None) -> pd.DataFrame:
    score_at = score_at or {}
    prices = prices or {}
    rows = []
    for timestamp in times:
        close = prices.get(timestamp, (100.0, 100.1, 99.9, 100.0))
        rows.append({
            "datetime": f"{day}T{timestamp}:00",
            "f_open": close[0],
            "f_high": close[1],
            "f_low": close[2],
            "f_close": close[3],
            "score": score_at.get(timestamp, float("nan")),
        })
    return pd.DataFrame(rows)


def _calendar(*days: str, expiry: str | None = None) -> pd.DataFrame:
    expiry = expiry or "2099-12-31"
    return pd.DataFrame({
        "trade_date": list(days),
        "front_expiry": [expiry for _ in days],
        "front_instrument_id": [f"NIFTY-FUT-{day}" for day in days],
    })


def _run(rows: pd.DataFrame, calendar: pd.DataFrame, *, threshold: float = 0.5, config: ReplayConfig | None = None):
    return replay(rows, threshold, sleeve_capital=1_000_000.0, contract_calendar=calendar, config=config)


def test_double_touch_bar_uses_registered_stop_wins_resolution():
    day = RULES["dates"]["base"]
    rows = _bars(
        day,
        ["09:45", "09:46"],
        score_at={"09:45": 1.0},
        prices={"09:46": (100.0, 100.5, 99.6, 100.0)},
    )
    result = _run(rows, _calendar(day))
    assert result.trades.iloc[0].exit_reason == "STOP"


def test_entry_bar_ignores_decision_bar_touch_and_honors_entry_bar_touch():
    day = RULES["dates"]["base"]
    rows = _bars(
        day,
        ["09:45", "09:46", "09:47"],
        score_at={"09:45": 1.0},
        prices={
            "09:45": (100.0, 100.5, 99.0, 100.0),
            "09:46": (100.0, 100.5, 100.0, 100.2),
            "09:47": (100.2, 100.3, 100.1, 100.2),
        },
    )
    result = _run(rows, _calendar(day))
    assert result.trades.iloc[0].exit_reason == "TARGET"
    assert result.trades.iloc[0].entry_datetime.hour == 9
    assert result.trades.iloc[0].entry_datetime.minute == 46


def test_decision_window_includes_1429_and_1454_but_rejects_1455():
    days = ["2024-06-03", "2024-06-04", "2024-06-05"]
    frames = [
        _bars(day, ["14:29", "14:30", "14:31"], score_at={"14:29": 1.0})
        for day in days[:1]
    ]
    frames.append(_bars(days[1], ["14:54", "14:55", "14:56"], score_at={"14:54": 1.0}))
    frames.append(_bars(days[2], ["14:54", "14:55", "14:56"], score_at={"14:55": 1.0}))
    result = _run(pd.concat(frames, ignore_index=True), _calendar(*days))
    assert result.trades.decision_datetime.dt.strftime("%H:%M").tolist() == ["14:29", "14:54"]
    assert len(result.daily) == 3


def test_lunch_exclusion_has_both_edges():
    days = ["2024-06-03", "2024-06-04", "2024-06-05", "2024-06-06"]
    rows = pd.concat(
        [
            _bars(
                day,
                ["12:00", "12:01", "12:02"] if minute == "12:00" else [minute, "12:00", "12:01"],
                score_at={minute: 1.0},
            )
            for day, minute in zip(days, ["10:59", "11:00", "11:59", "12:00"])
        ],
        ignore_index=True,
    )
    result = _run(rows, _calendar(*days))
    assert result.trades.decision_datetime.dt.strftime("%H:%M").tolist() == ["10:59", "12:00"]


def test_realized_minus_five_r_halts_from_next_bar():
    day = RULES["dates"]["base"]
    times = [(datetime.fromisoformat(f"{day}T09:45") + timedelta(minutes=i)).strftime("%H:%M") for i in range(13)]
    scores = {time: 1.0 - i / 100 for i, time in enumerate(times[:6])}
    prices = {time: (100.0, 100.0, 99.0, 99.0) for time in times}
    rows = _bars(day, times, score_at=scores, prices=prices)
    result = _run(rows, _calendar(day), config=ReplayConfig(max_trades_per_day=10))
    assert len(result.trades) == 5
    assert result.trades.iloc[-1].realized_r == -1.0
    assert result.daily.loc[date.fromisoformat(day), "trade_count"] == 5


def test_expiry_day_exits_at_close_and_next_day_uses_rolled_front_contract():
    expiry, rolled = RULES["dates"]["expiry"], RULES["dates"]["roll"]
    rows = pd.concat(
        [
            _bars(expiry, ["14:53", "14:54", "14:55"], score_at={"14:53": 1.0}),
            _bars(rolled, ["09:45", "09:46", "09:47"], score_at={"09:45": 1.0}),
        ],
        ignore_index=True,
    )
    calendar = pd.DataFrame({
        "trade_date": [expiry, rolled],
        "front_expiry": [expiry, "2024-07-25"],
        "front_instrument_id": ["NIFTY-JUN", "NIFTY-JUL"],
    })
    result = _run(rows, calendar)
    assert result.trades.front_expiry.astype(str).tolist() == [expiry, "2024-07-25"]
    assert result.trades.contract_id.tolist() == ["NIFTY-JUN", "NIFTY-JUL"]
    assert result.trades.exit_reason.iloc[0] == "EXPIRY"


def test_acceptance_is_chronological_with_score_ties_by_timestamp():
    equal_day, unequal_day = "2024-06-03", "2024-06-04"
    equal = _bars(equal_day, ["09:45", "09:46", "09:47"], score_at={"09:45": 0.9, "09:46": 0.9})
    unequal = _bars(unequal_day, ["09:45", "09:46", "09:47"], score_at={"09:45": 0.5, "09:46": 0.9})
    result = _run(pd.concat([equal, unequal], ignore_index=True), _calendar(equal_day, unequal_day))
    assert result.trades.decision_datetime.dt.strftime("%Y-%m-%d %H:%M").tolist() == [
        f"{equal_day} 09:45",
        f"{unequal_day} 09:45",
    ]


def test_registered_eligibility_columns_gate_above_threshold_candidates():
    cases = [
        ("2024-06-03", "is_decision_eligible"),
        ("2024-06-04", "regime_eligible"),
        ("2024-06-05", "label_window_contiguous"),
    ]
    for day, column in cases:
        rows = _bars(day, ["09:45", "09:46"], score_at={"09:45": 1.0})
        rows[column] = False
        assert _run(rows, _calendar(day)).trades.empty


def test_timeout_is_minimum_of_sixty_bars_and_session_end():
    short_day = "2024-06-03"
    short_times = [(datetime.fromisoformat(f"{short_day}T14:29") + timedelta(minutes=i)).strftime("%H:%M") for i in range(4)]
    long_day = "2024-06-04"
    long_times = [(datetime.fromisoformat(f"{long_day}T09:45") + timedelta(minutes=i)).strftime("%H:%M") for i in range(63)]
    rows = pd.concat(
        [_bars(short_day, short_times, score_at={short_times[0]: 1.0}), _bars(long_day, long_times, score_at={long_times[0]: 1.0})],
        ignore_index=True,
    )
    result = _run(rows, _calendar(short_day, long_day))
    assert result.trades.exit_reason.tolist() == ["TIMEOUT", "TIMEOUT"]
    assert result.trades.exit_datetime.dt.strftime("%H:%M").tolist() == ["14:32", "10:45"]


def test_no_new_entry_while_position_is_open():
    day = RULES["dates"]["base"]
    times = [(datetime.fromisoformat(f"{day}T09:45") + timedelta(minutes=i)).strftime("%H:%M") for i in range(5)]
    rows = _bars(day, times, score_at={times[0]: 1.0, times[1]: 0.9})
    result = _run(rows, _calendar(day))
    assert len(result.trades) == 1


def test_zero_trade_day_is_present_with_zero_policy_return():
    traded, zero = RULES["dates"]["base"], RULES["dates"]["zero"]
    rows = _bars(traded, ["09:45", "09:46"], score_at={"09:45": 1.0})
    rows = pd.concat([rows, _bars(zero, ["11:00", "11:01"])], ignore_index=True)
    result = _run(rows, _calendar(traded, zero))
    assert result.daily.loc[date.fromisoformat(zero), "trade_count"] == 0
    assert result.daily.loc[date.fromisoformat(zero), "policy_return_bps"] == 0.0


def test_replay_is_byte_identical_on_repeated_runs():
    day = RULES["dates"]["base"]
    rows = _bars(day, ["09:45", "09:46", "09:47"], score_at={"09:45": 1.0})
    calendar = _calendar(day)
    first, second = _run(rows, calendar), _run(rows, calendar)
    assert first.trades.to_json(date_format="iso", orient="split") == second.trades.to_json(date_format="iso", orient="split")
    assert first.daily.to_json(date_format="iso", orient="split") == second.daily.to_json(date_format="iso", orient="split")


def test_stress_recost_changes_net_without_changing_trade_set():
    day = RULES["dates"]["base"]
    rows = _bars(
        day,
        ["09:45", "09:46", "09:47"],
        score_at={"09:45": 1.0},
        prices={"09:46": (100.0, 100.2, 100.0, 100.1), "09:47": (100.1, 100.2, 100.0, 101.0)},
    )
    result = _run(rows, _calendar(day))
    assert len(result.trades) == 1
    assert result.daily.loc[date.fromisoformat(day), "stress_3_5_return_bps"] < result.daily.loc[date.fromisoformat(day), "policy_return_bps"]
    assert result.trades[["decision_datetime", "entry_datetime", "exit_datetime"]].to_dict("records") == [
        {"decision_datetime": pd.Timestamp(f"{day} 09:45"), "entry_datetime": pd.Timestamp(f"{day} 09:46"), "exit_datetime": pd.Timestamp(f"{day} 09:47")}
    ]
