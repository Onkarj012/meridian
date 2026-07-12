from __future__ import annotations

import pandas as pd
import pytest

from contracts.sleeve_f_labels import (
    generate_execution_consistent_labels,
    generate_execution_consistent_labels_for_sessions,
    generate_legacy_parity_labels,
)


def bars(rows: list[tuple[float, float, float, float]], start: str = "2024-01-02 09:15") -> pd.DataFrame:
    index = pd.date_range(start, periods=len(rows), freq="min")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], dtype=float).assign(datetime=index)


def session_bars() -> pd.DataFrame:
    return bars([(100.0, 100.0, 100.0, 100.0)] * 375)


def test_execution_enters_at_next_open_and_uses_entry_bar_for_touch() -> None:
    frame = bars([(99, 99, 99, 100), (101, 101.5, 100.8, 101.2), (101, 101, 101, 101)])
    row = generate_execution_consistent_labels(frame).iloc[0]
    assert row.entry_price == 101
    assert row.exit_reason == "target"
    assert row.exit_price == pytest.approx(101 * 1.004)
    assert row.exit_bar_offset == 1


def test_execution_barriers_are_measured_from_entry_not_decision_close() -> None:
    frame = bars([(100, 100, 100, 100), (110, 110.1, 109.9, 110), (110, 110.3, 109.9, 110)])
    row = generate_execution_consistent_labels(frame).iloc[0]
    assert row.entry_price == 110
    assert row.exit_reason == "timeout"


def test_execution_ohlc_stop_wins_double_touch_and_reports_excursions() -> None:
    frame = bars([(100, 100, 100, 100), (100, 100.5, 99.5, 100)])
    row = generate_execution_consistent_labels(frame).iloc[0]
    assert row.exit_reason == "stop"
    assert row.exit_price == pytest.approx(99.7)
    assert row.gross_return_bps == pytest.approx(-30)
    assert row.mfe_bps == pytest.approx(50)
    assert row.mae_bps == pytest.approx(-50)


def test_execution_timeout_at_sixtieth_bar_close() -> None:
    frame = bars([(100, 100, 100, 100)] * 61)
    frame.loc[60, ["open", "high", "low", "close"]] = [100, 100.1, 99.9, 100.2]
    row = generate_execution_consistent_labels(frame).iloc[0]
    assert row.exit_reason == "timeout"
    assert row.exit_bar_offset == 60
    assert row.exit_price == pytest.approx(100.2)
    assert row.gross_return_bps == pytest.approx(20)


def test_execution_late_entries_truncate_at_last_same_session_bar() -> None:
    frame = session_bars()
    frame.loc[374, ["open", "high", "low", "close"]] = [100, 100.1, 99.9, 100.15]
    labels = generate_execution_consistent_labels(frame)
    at_1429 = labels.loc[labels.datetime.dt.strftime("%H:%M") == "14:29"].iloc[0]
    at_1454 = labels.loc[labels.datetime.dt.strftime("%H:%M") == "14:54"].iloc[0]
    assert at_1429.exit_reason == "timeout"
    assert at_1429.exit_bar_offset == 60
    assert at_1454.exit_reason == "timeout"
    assert at_1454.exit_bar_offset == 35
    assert at_1454.exit_price == pytest.approx(100.15)


def test_execution_never_uses_next_session_and_marks_last_bar_excluded() -> None:
    first = session_bars()
    second = session_bars()
    second["datetime"] += pd.Timedelta(days=1)
    second.loc[0, ["open", "high", "low", "close"]] = [200, 300, 200, 300]
    labels = generate_execution_consistent_labels_for_sessions(pd.concat([first, second], ignore_index=True))
    final_first = labels[labels.datetime == first.datetime.iloc[-1]].iloc[0]
    assert final_first.label_excluded
    assert final_first.exit_reason == "no_next_open"


def test_execution_cost_is_injected_and_net_tie_is_not_profitable() -> None:
    frame = bars([(100, 100, 100, 100), (100, 100, 100, 100.1)])
    row = generate_execution_consistent_labels(frame, cost_bps_fn=lambda _day: 10.0).iloc[0]
    assert row.gross_return_bps == pytest.approx(10)
    assert row.cost_bps == 10
    assert row.net_return_bps == pytest.approx(0)
    assert row.net_return_r == pytest.approx(0)
    assert row.net_label == 0


def test_legacy_is_close_touch_only_with_archive_semantics() -> None:
    frame = bars([(100, 100, 100, 100), (200, 101, 99, 100), (100, 100, 100, 100.4), (100, 100, 100, 99.7)])
    labels = generate_legacy_parity_labels(frame)
    assert labels.long_label.tolist() == [1, 1, 0, 0]
    assert labels.short_label.tolist() == [0, 0, 1, 0]


def test_legacy_includes_the_sixtieth_future_close_but_not_ohlc_touches() -> None:
    frame = bars([(100, 100, 100, 100)] * 62)
    frame.loc[1, ["high", "low"]] = [101, 99]
    frame.loc[60, "close"] = 100.4
    assert generate_legacy_parity_labels(frame).long_label.iloc[0] == 1
