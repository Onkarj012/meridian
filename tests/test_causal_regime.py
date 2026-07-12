from __future__ import annotations

import numpy as np
import pandas as pd

from features.causal_regime import add_regime_causal, regime_config_hash
from features.sleeve_f_router import add_regime


def _frame(session_count: int, minutes: tuple[int, ...] = (600,)) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=session_count)
    rows = [
        {
            "trade_date": trade_date,
            "datetime": trade_date + pd.Timedelta(minutes=minute),
            "minute_of_day": minute,
            "realized_vol_30m": float(session + 1),
            "ema_slope": 0.0,
            "ret_30m": 0.0,
        }
        for session, trade_date in enumerate(dates)
        for minute in minutes
    ]
    return pd.DataFrame(rows)


def test_future_sessions_cannot_change_an_earlier_session_regime():
    frame = _frame(45, (600, 601))
    frame["realized_vol_30m"] = 1.0 + (frame.index.to_numpy() % 10)
    frame["ema_slope"] = 0.002
    frame["ret_30m"] = 0.01
    target_date = frame["trade_date"].iloc[30 * 2]

    baseline = add_regime_causal(frame)
    mutated = frame.copy()
    mutated.loc[mutated["trade_date"] > target_date, "realized_vol_30m"] = 1_000_000.0
    changed = add_regime_causal(mutated)

    target = baseline["trade_date"] == target_date
    pd.testing.assert_frame_equal(
        baseline.loc[target, ["regime", "regime_eligible"]],
        changed.loc[target, ["regime", "regime_eligible"]],
    )


def test_current_session_rv_is_excluded_from_its_own_thresholds():
    frame = _frame(22)
    target_session = 20
    target = frame.index == target_session
    frame.loc[target, "realized_vol_30m"] = 5.6

    baseline = add_regime_causal(frame)
    mutated = frame.copy()
    mutated.loc[target, "realized_vol_30m"] = 5.8
    changed = add_regime_causal(mutated)

    expected_p25 = frame.loc[frame.index < target_session, "realized_vol_30m"].quantile(0.25)
    assert expected_p25 == 5.75
    assert baseline.loc[target, "regime"].item() == "compression"
    assert changed.loc[target, "regime"].item() == "range"
    assert baseline.loc[target, "regime_eligible"].item()
    assert changed.loc[target, "regime_eligible"].item()


def test_minimum_valid_prior_sessions_controls_eligibility():
    frame = _frame(21)
    result = add_regime_causal(frame)

    assert result.loc[19, "regime"] == "ineligible"
    assert not result.loc[19, "regime_eligible"]
    assert result.loc[20, "regime"] != "ineligible"
    assert result.loc[20, "regime_eligible"]


def test_missing_current_signal_is_ineligible_with_sufficient_prior_sessions():
    frame = _frame(21)
    frame.loc[20, "ema_slope"] = np.nan

    result = add_regime_causal(frame)

    assert result.loc[20, "regime"] == "ineligible"
    assert not result.loc[20, "regime_eligible"]


def test_thresholds_are_matched_to_minute_of_day():
    frame = _frame(22, (600, 601))
    frame.loc[frame["minute_of_day"] == 601, "realized_vol_30m"] += 100.0
    target_date = frame["trade_date"].iloc[-1]
    target = frame["trade_date"] == target_date
    frame.loc[target, "realized_vol_30m"] = 10.0

    result = add_regime_causal(frame)

    assert result.loc[target & (result["minute_of_day"] == 600), "regime"].item() == "range"
    assert result.loc[target & (result["minute_of_day"] == 601), "regime"].item() == "compression"


def test_eligible_labels_match_incumbent_when_thresholds_coincide():
    frame = _frame(25, (600, 601))
    frame["realized_vol_30m"] = 1.0

    causal = add_regime_causal(frame)
    incumbent = add_regime(frame)
    eligible = causal["regime_eligible"]

    assert eligible.any()
    pd.testing.assert_series_equal(
        causal.loc[eligible, "regime"],
        incumbent.loc[eligible, "regime"],
        check_names=False,
    )


def test_regime_config_hash_is_pinned():
    assert regime_config_hash() == "75151f226ced8002cf6c76930fcdd273542c88315a8180a913fc74a45c06507a"


def test_precedence_drives_classification_and_mixed_frame_is_unchanged():
    # With a flat prior distribution, quantiles collapse to the prior value,
    # making each regime branch easy to hit deterministically.
    frame = _frame(23, (600, 601, 602, 603, 604, 605))
    frame["realized_vol_30m"] = 10.0
    frame["ema_slope"] = 0.0
    frame["ret_30m"] = 0.0

    target_session = frame["trade_date"].unique()[22]
    target = frame["trade_date"] == target_session
    settings = {
        600: (5.0, 0.0, 0.0),      # compression
        601: (15.0, 0.0, 0.0),     # expansion
        602: (5.0, -0.002, -0.01), # trend_dn (not compression because ret is large)
        603: (5.0, 0.002, 0.01),   # trend_up
        604: (10.0, 0.0, 0.0),     # range
        605: (10.0, np.nan, 0.0),  # ineligible (missing input)
    }
    for minute, (rv, slope, ret) in settings.items():
        frame.loc[target & (frame["minute_of_day"] == minute), ["realized_vol_30m", "ema_slope", "ret_30m"]] = [rv, slope, ret]

    result = add_regime_causal(frame)
    by_minute = result.loc[target].set_index("minute_of_day")["regime"]
    assert by_minute.loc[600] == "compression"
    assert by_minute.loc[601] == "expansion"
    assert by_minute.loc[602] == "trend_dn"
    assert by_minute.loc[603] == "trend_up"
    assert by_minute.loc[604] == "range"
    assert by_minute.loc[605] == "ineligible"

    # Sessions without enough valid prior observations remain ineligible.
    early_session = frame["trade_date"].unique()[19]
    assert (result.loc[result["trade_date"] == early_session, "regime"] == "ineligible").all()

    assert regime_config_hash() == "75151f226ced8002cf6c76930fcdd273542c88315a8180a913fc74a45c06507a"


def test_causal_regime_builder_is_deterministic():
    frame = _frame(30, (600, 601, 602))
    pd.testing.assert_frame_equal(add_regime_causal(frame), add_regime_causal(frame))
