"""Sleeve F minute-bar label generators.

The legacy generator deliberately preserves the archived close-only label
semantics.  The execution generator is separate so prospective models cannot
accidentally inherit that look-ahead-friendly convention.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date

import numpy as np
import pandas as pd


LEGACY_TARGET_PCT = 0.0040
LEGACY_STOP_PCT = 0.0030
LEGACY_HORIZON_BARS = 60
EXECUTION_TARGET_PCT = 0.0040
EXECUTION_STOP_PCT = 0.0030
EXECUTION_HORIZON_BARS = 60
RISK_UNIT_BPS = 30.0


def generate_legacy_parity_labels(session_bars: pd.DataFrame) -> pd.DataFrame:
    """Reproduce ``sprint1_futures.build_barrier_labels`` for one session.

    This intentionally checks *only future closes* (not high/low), starts the
    horizon on the next bar, and leaves a simultaneous close-touch as zero.
    """
    bars = _normalise_bars(session_bars, require_ohlc=False)
    _require_one_session(bars)
    prices = bars["close"].to_numpy(dtype=np.float64)
    long_label = np.zeros(len(bars), dtype=np.int8)
    short_label = np.zeros(len(bars), dtype=np.int8)

    for index, entry in enumerate(prices):
        end = min(len(prices), index + 1 + LEGACY_HORIZON_BARS)
        if end <= index + 1:
            continue
        window = prices[index + 1 : end]
        target_long = entry * (1 + LEGACY_TARGET_PCT)
        stop_long = entry * (1 - LEGACY_STOP_PCT)
        target_hits = np.where(window >= target_long)[0]
        stop_hits = np.where(window <= stop_long)[0]
        if target_hits.size and (not stop_hits.size or target_hits[0] < stop_hits[0]):
            long_label[index] = 1

        target_short = entry * (1 - LEGACY_TARGET_PCT)
        stop_short = entry * (1 + LEGACY_STOP_PCT)
        target_hits = np.where(window <= target_short)[0]
        stop_hits = np.where(window >= stop_short)[0]
        if target_hits.size and (not stop_hits.size or target_hits[0] < stop_hits[0]):
            short_label[index] = 1

    return pd.DataFrame(
        {
            "datetime": bars["datetime"].to_numpy(),
            "trade_date": bars["trade_date"].to_numpy(),
            "fut_close": prices,
            "long_label": long_label,
            "short_label": short_label,
        }
    )


def generate_legacy_parity_labels_for_sessions(bars: pd.DataFrame) -> pd.DataFrame:
    """Generate archived-compatible labels independently for every session."""
    normalised = _normalise_bars(bars, require_ohlc=False)
    return pd.concat(
        [generate_legacy_parity_labels(day) for _, day in normalised.groupby("trade_date", sort=True)],
        ignore_index=True,
    ) if not normalised.empty else _empty_legacy_frame()


def generate_execution_consistent_labels(
    session_bars: pd.DataFrame,
    cost_bps_fn: Callable[[date], float] | None = None,
) -> pd.DataFrame:
    """Label each decision bar using a next-open, same-session long replay.

    The next bar is both the fill bar and the first bar eligible to touch a
    barrier. ``exit_bar_offset`` is measured from the decision bar, so a
    timeout at the 60th available execution bar has offset 60.
    """
    bars = _normalise_bars(session_bars, require_ohlc=True)
    _require_one_session(bars)
    cost_bps_fn = cost_bps_fn or (lambda _trade_date: 0.0)
    n = len(bars)
    records: list[dict[str, object]] = []

    for decision_index, decision in bars.iterrows():
        common: dict[str, object] = {
            "datetime": decision["datetime"],
            "trade_date": decision["trade_date"],
            "decision_close": float(decision["close"]),
        }
        if decision_index + 1 >= n:
            records.append(_excluded_execution_record(common, "no_next_open"))
            continue

        entry_index = decision_index + 1
        entry = float(bars.at[entry_index, "open"])
        if not np.isfinite(entry) or entry <= 0:
            records.append(_excluded_execution_record(common, "invalid_entry"))
            continue

        target = entry * (1 + EXECUTION_TARGET_PCT)
        stop = entry * (1 - EXECUTION_STOP_PCT)
        last_index = min(n - 1, decision_index + EXECUTION_HORIZON_BARS)
        mfe_bps = 0.0
        mae_bps = 0.0
        exit_price: float | None = None
        exit_reason = "timeout"
        exit_offset = last_index - decision_index

        for bar_index in range(entry_index, last_index + 1):
            high = float(bars.at[bar_index, "high"])
            low = float(bars.at[bar_index, "low"])
            mfe_bps = max(mfe_bps, (high - entry) / entry * 10_000)
            mae_bps = min(mae_bps, (low - entry) / entry * 10_000)
            hit_target = high >= target
            hit_stop = low <= stop
            # A bar's OHLC cannot order two touches; conservatively stop first.
            if hit_stop:
                exit_price, exit_reason = stop, "stop"
                exit_offset = bar_index - decision_index
                break
            if hit_target:
                exit_price, exit_reason = target, "target"
                exit_offset = bar_index - decision_index
                break

        if exit_price is None:
            exit_price = float(bars.at[last_index, "close"])

        gross_bps = (exit_price - entry) / entry * 10_000
        cost_bps = float(cost_bps_fn(pd.Timestamp(decision["trade_date"]).date()))
        net_bps = gross_bps - cost_bps
        records.append(
            {
                **common,
                "entry_price": entry,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "exit_bar_offset": exit_offset,
                "gross_return_bps": gross_bps,
                "gross_return_r": gross_bps / RISK_UNIT_BPS,
                "mfe_bps": mfe_bps,
                "mae_bps": mae_bps,
                "cost_bps": cost_bps,
                "net_return_bps": net_bps,
                "net_return_r": net_bps / RISK_UNIT_BPS,
                "net_label": int(net_bps > 0),
                "label_excluded": False,
            }
        )
    return pd.DataFrame.from_records(records, columns=_execution_columns())


def generate_execution_consistent_labels_for_sessions(
    bars: pd.DataFrame,
    cost_bps_fn: Callable[[date], float] | None = None,
) -> pd.DataFrame:
    """Run the execution-consistent generator independently per session."""
    normalised = _normalise_bars(bars, require_ohlc=True)
    return pd.concat(
        [generate_execution_consistent_labels(day, cost_bps_fn) for _, day in normalised.groupby("trade_date", sort=True)],
        ignore_index=True,
    ) if not normalised.empty else pd.DataFrame(columns=_execution_columns())


def _normalise_bars(bars: pd.DataFrame, *, require_ohlc: bool) -> pd.DataFrame:
    """Accept archive CSV columns or the router's ``f_``-prefixed columns."""
    aliases = {
        "open": ("open", "f_open"),
        "high": ("high", "f_high"),
        "low": ("low", "f_low"),
        "close": ("close", "f_close", "fut_close"),
    }
    raw = bars.copy()
    if "datetime" in raw:
        timestamps = pd.to_datetime(raw["datetime"])
    elif {"date", "time"}.issubset(raw.columns):
        timestamps = pd.to_datetime(raw["date"].astype(str) + " " + raw["time"].astype(str))
    else:
        raise ValueError("bars need datetime or both date and time columns")
    out = pd.DataFrame({"datetime": timestamps})
    needed = aliases if require_ohlc else {"close": aliases["close"]}
    for name, candidates in needed.items():
        source = next((column for column in candidates if column in raw.columns), None)
        if source is None:
            raise ValueError(f"bars need one of {candidates} for {name}")
        out[name] = pd.to_numeric(raw[source], errors="raise")
    out["trade_date"] = out["datetime"].dt.normalize()
    return out.sort_values("datetime", kind="stable").reset_index(drop=True)


def _require_one_session(bars: pd.DataFrame) -> None:
    if bars["trade_date"].nunique() > 1:
        raise ValueError("session generator received more than one trade date; use the multi-session driver")


def _excluded_execution_record(common: dict[str, object], reason: str) -> dict[str, object]:
    return {
        **common,
        "entry_price": np.nan,
        "exit_price": np.nan,
        "exit_reason": reason,
        "exit_bar_offset": 0,
        "gross_return_bps": np.nan,
        "gross_return_r": np.nan,
        "mfe_bps": np.nan,
        "mae_bps": np.nan,
        "cost_bps": np.nan,
        "net_return_bps": np.nan,
        "net_return_r": np.nan,
        "net_label": 0,
        "label_excluded": True,
    }


def _execution_columns() -> list[str]:
    return [
        "datetime", "trade_date", "decision_close", "entry_price", "exit_price", "exit_reason",
        "exit_bar_offset", "gross_return_bps", "gross_return_r", "mfe_bps", "mae_bps", "cost_bps",
        "net_return_bps", "net_return_r", "net_label", "label_excluded",
    ]


def _empty_legacy_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["datetime", "trade_date", "fut_close", "long_label", "short_label"])
