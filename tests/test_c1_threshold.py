from __future__ import annotations

import numpy as np
import pandas as pd

from evidence.c1_replay import ReplayConfig
from policy.c1_threshold import fit_threshold, round_half_up


def _rows(scores_by_day: dict[str, list[float]]) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    calendar_rows = []
    for day, scores in scores_by_day.items():
        calendar_rows.append({"trade_date": day, "front_expiry": "2099-12-31"})
        for index in range(max(2, len(scores) + 1)):
            rows.append({
                "datetime": f"{day} 09:{45 + index:02d}:00",
                "f_open": 100.0,
                "f_high": 100.0,
                "f_low": 100.0,
                "f_close": 100.0,
                "score": scores[index] if index < len(scores) else np.nan,
            })
    return pd.DataFrame(rows), pd.DataFrame(calendar_rows)


def test_fit_selects_exact_threshold_from_replay_trade_counts() -> None:
    rows, calendar = _rows({
        "2024-06-03": [0.1],
        "2024-06-04": [0.2],
        "2024-06-05": [0.3],
    })

    result = fit_threshold(rows, contract_calendar=calendar)

    assert result.threshold == 0.2
    assert result.target == 2  # round_half_up(0.70 * 3) = 2
    assert result.achieved_count == 2


def test_round_half_up_does_not_use_bankers_rounding() -> None:
    assert round_half_up(0.5) == 1
    assert round_half_up(1.5) == 2


def test_tie_break_prefers_higher_threshold_and_fewer_executed_trades() -> None:
    rows, calendar = _rows({
        "2024-06-03": [0.9, 0.8, 0.7],
        "2024-06-04": [0.7],
        "2024-06-05": [0.7],
    })

    result = fit_threshold(
        rows,
        activity_rate=0.5,  # target round_half_up(1.5) = 2
        contract_calendar=calendar,
        replay_config=ReplayConfig(horizon_bars=3),
    )

    assert result.target == 2
    assert result.threshold == 0.9
    assert result.achieved_count == 1


def test_counts_are_from_replay_after_exclusivity_and_three_trade_cap() -> None:
    rows, calendar = _rows({"2024-06-03": [0.9, 0.8, 0.7, 0.6, 0.5]})

    result = fit_threshold(
        rows,
        contract_calendar=calendar,
        replay_config=ReplayConfig(horizon_bars=2, max_trades_per_day=3),
    )

    assert result.candidates_evaluated == (0.5, 0.6, 0.7, 0.8, 0.9)
    assert result.achieved_counts == (3, 2, 2, 1, 1)
    assert result.achieved_count <= 3
