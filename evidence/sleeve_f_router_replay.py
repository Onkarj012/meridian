"""Replay the incumbent Sleeve F router and its conservative causal variant.

``legacy_parity`` deliberately uses each eligible day's complete score
distribution for thresholds and close-only signal-bar fills.  Its threshold
selection is lookahead-biased and it is unpromotable.

``causal`` uses the immediately preceding eligible trading day's score 85th
and 95th percentiles; its first available day cannot trade.  Entries fill at
the following bar's open.  It evaluates the entry bar and the following 59
bars using OHLC first-touch barriers; a bar crossing both barriers exits at the
stop, the conservative convention.  Regime eligibility remains noncausal in
both variants because it comes from full-frame quantiles.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time as dtime
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

from features.sleeve_f_router import FUTURES_FEATURES

TARGET_PCT = 0.0040
STOP_PCT = 0.0030
HORIZON = 60
LOT = 50
COSTS_INR = 105.0
STOP_FLOOR = -3000.0
DAILY_HALT = -15000.0
INTRADAY_CUM_HALT = -9000.0  # Deliberately not applied: absent from forward_walk.py.
MAX_TRADES = 3
SelectionVariant = Literal["legacy_parity", "causal"]


@dataclass(frozen=True)
class ReplayConfig:
    variant: SelectionVariant = "legacy_parity"
    target_pct: float = TARGET_PCT
    stop_pct: float = STOP_PCT
    horizon: int = HORIZON
    lot: int = LOT
    costs_inr: float = COSTS_INR
    stop_floor: float = STOP_FLOOR
    daily_halt: float = DAILY_HALT
    max_trades: int = MAX_TRADES


def load_router_model(model_path: Path | str | None = None):
    """Load the exact serialized single-tree LightGBM model; never retrain."""
    import lightgbm as lgb

    path = Path(model_path) if model_path else Path(__file__).resolve().parents[1] / "models/artifacts/sleeve_f/final_long.lgb"
    return lgb.Booster(model_file=str(path))


def eligible_rows(features: pd.DataFrame) -> pd.DataFrame:
    """Apply the incumbent's time and compression filters before scoring."""
    df = features.copy()
    time = df["datetime"].dt.time
    minute_after_open = df["minute_of_day"] - 555
    return df[
        (minute_after_open >= 30)
        & (time < dtime(14, 55))
        & ~((time >= dtime(11, 0)) & (time < dtime(12, 0)))
        & ~df["regime"].isin({"compression"})
    ].copy()


def score_and_select(features: pd.DataFrame, model, variant: SelectionVariant) -> pd.DataFrame:
    """Score filtered rows and annotate selection using the requested policy."""
    df = eligible_rows(features)
    df["long_score"] = model.predict(df[FUTURES_FEATURES])
    if variant == "legacy_parity":
        df["threshold_85"] = df.groupby("trade_date")["long_score"].transform(lambda values: values.quantile(0.85))
        df["threshold_95"] = df.groupby("trade_date")["long_score"].transform(lambda values: values.quantile(0.95))
    elif variant == "causal":
        per_day = df.groupby("trade_date", sort=True)["long_score"].quantile([0.85, 0.95]).unstack()
        per_day.columns = ["threshold_85", "threshold_95"]
        prior = per_day.shift(1)
        df = df.join(prior, on="trade_date")
    else:
        raise ValueError(f"unknown selection variant: {variant}")
    df["take_long"] = df["long_score"] >= df["threshold_85"]
    df["size_mult"] = np.where(df["long_score"] >= df["threshold_95"], 1.5, 1.0)
    return df


def _net_pnl(entry_px: float, exit_px: float, size_mult: float, config: ReplayConfig) -> float:
    return (exit_px - entry_px) * config.lot * size_mult - config.costs_inr * size_mult


def simulate_legacy_trade(entry_index: int, bars: pd.DataFrame, entry_px: float, size_mult: float, config: ReplayConfig) -> dict:
    """Exact close-only forward_walk.py trade simulator."""
    target_px = entry_px * (1 + config.target_pct)
    stop_px = entry_px * (1 - config.stop_pct)
    walk = bars.iloc[entry_index + 1:min(len(bars), entry_index + 1 + config.horizon)]
    exit_px: float | None = None
    exit_reason: str | None = None
    for _, row in walk.iterrows():
        close = float(row["f_close"])
        if close >= target_px:
            exit_px, exit_reason = target_px, "TARGET"
            break
        if close <= stop_px:
            exit_px, exit_reason = stop_px, "STOP"
            break
    if exit_px is None:
        if walk.empty:
            exit_px, exit_reason = entry_px, "NO_BARS"
        else:
            exit_px, exit_reason = float(walk["f_close"].iloc[-1]), "TIME"
    net = _net_pnl(entry_px, exit_px, size_mult, config)
    if net < config.stop_floor:
        net = config.stop_floor
    return {"exit_px": exit_px, "exit_reason": exit_reason, "net_pnl_inr": net, "size_mult": size_mult}


def simulate_causal_trade(entry_index: int, bars: pd.DataFrame, size_mult: float, config: ReplayConfig) -> dict:
    """Conservative next-open entry and OHLC first-touch exit simulator."""
    entry_px = float(bars["f_open"].iat[entry_index])
    target_px = entry_px * (1 + config.target_pct)
    stop_px = entry_px * (1 - config.stop_pct)
    walk = bars.iloc[entry_index:min(len(bars), entry_index + config.horizon)]
    exit_px: float | None = None
    exit_reason: str | None = None
    for _, row in walk.iterrows():
        # Gap-throughs are filled at the open; an intrabar double touch is a stop.
        if float(row["f_open"]) <= stop_px or float(row["f_low"]) <= stop_px:
            exit_px, exit_reason = stop_px, "STOP"
            break
        if float(row["f_open"]) >= target_px or float(row["f_high"]) >= target_px:
            exit_px, exit_reason = target_px, "TARGET"
            break
    if exit_px is None:
        if walk.empty:
            exit_px, exit_reason = entry_px, "NO_BARS"
        else:
            exit_px, exit_reason = float(walk["f_close"].iloc[-1]), "TIME"
    net = _net_pnl(entry_px, exit_px, size_mult, config)
    if net < config.stop_floor:
        net = config.stop_floor
    return {"entry_px": entry_px, "exit_px": exit_px, "exit_reason": exit_reason, "net_pnl_inr": net, "size_mult": size_mult}


def replay(features: pd.DataFrame, model, *, variant: SelectionVariant = "legacy_parity", config: ReplayConfig | None = None) -> tuple[pd.DataFrame, dict]:
    """Run the supplied selection variant and return trades plus immutable metadata."""
    config = config or ReplayConfig(variant=variant)
    if config.variant != variant:
        raise ValueError("config variant must match variant")
    selected = score_and_select(features, model, variant)
    candidates = selected[selected["take_long"]].sort_values(["trade_date", "datetime"]).reset_index(drop=True)
    day_bars = {day: group.sort_values("datetime").reset_index(drop=True) for day, group in features.groupby("trade_date")}
    results: list[dict] = []
    daily_pnl: dict[pd.Timestamp, float] = {}
    daily_count: dict[pd.Timestamp, int] = {}
    for _, row in candidates.iterrows():
        day = row["trade_date"]
        if daily_count.get(day, 0) >= config.max_trades or daily_pnl.get(day, 0.0) <= config.daily_halt:
            continue
        bars = day_bars[day]
        index_array = bars.index[bars["datetime"] == row["datetime"]]
        if len(index_array) == 0:
            continue
        signal_index = int(index_array[0])
        if variant == "legacy_parity":
            entry_px = float(bars["f_close"].iat[signal_index])
            simulation = simulate_legacy_trade(signal_index, bars, entry_px, float(row["size_mult"]), config)
        else:
            fill_index = signal_index + 1
            if fill_index >= len(bars):
                continue
            simulation = simulate_causal_trade(fill_index, bars, float(row["size_mult"]), config)
            entry_px = simulation.pop("entry_px")
        results.append({
            "trade_date": day,
            "datetime": row["datetime"],
            "regime": row["regime"],
            "long_score": float(row["long_score"]),
            "entry_px": entry_px,
            **simulation,
        })
        daily_pnl[day] = daily_pnl.get(day, 0.0) + simulation["net_pnl_inr"]
        daily_count[day] = daily_count.get(day, 0) + 1
    metadata = {
        "variant": variant,
        "selection_lookahead": variant == "legacy_parity",
        "regime_source": "add_regime (noncausal full-frame quantiles)",
        "residual_lookahead": ["regime_eligibility"],
        "lookahead": True,
        "promotable": False,
        "selection_rule": "same eligible day full-day score percentiles" if variant == "legacy_parity" else "previous eligible trading day's score percentiles",
        "fill_exit_rule": "signal-bar close / subsequent close-only, target first" if variant == "legacy_parity" else "next-bar open / OHLC first-touch, stop first on double touch",
        "intraday_cum_halt_applied": False,
    }
    return pd.DataFrame(results), metadata


def summarize(trades: pd.DataFrame) -> dict:
    """Metrics used in bridge and causal reports."""
    exit_counts = {reason: int(count) for reason, count in trades["exit_reason"].value_counts().items()} if not trades.empty else {}
    daily = trades.groupby("trade_date")["net_pnl_inr"].sum() if not trades.empty else pd.Series(dtype=float)
    sharpe = float(daily.mean() / daily.std() * np.sqrt(252)) if len(daily) > 1 and daily.std() > 0 else 0.0
    return {"trade_count": int(len(trades)), "net_pnl_inr": float(trades["net_pnl_inr"].sum()) if not trades.empty else 0.0, "exit_counts": exit_counts, "sharpe_daily_ann": sharpe}
