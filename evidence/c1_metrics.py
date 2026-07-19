"""Pure C1 metrics over replay artifacts.

The functions here do not replay or alter trades.  They normalize fold replay
artifacts, assert strictly-OOS stitching, and calculate registered headline
metrics plus explicitly diagnostic stability slices.
"""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping

import pandas as pd

from evidence.c1_replay import ReplayResult
from evidence.stats import (
    deflated_sharpe_ratio,
    moving_block_bootstrap_ci,
    paired_moving_block_bootstrap_ci,
    sharpe,
)
from policy.era_costs import ERA_TABLE


MBB_BLOCK_SIZE = 20
MBB_DRAWS = 10_000
MBB_CONFIDENCE = 0.95
DSR_K = 95
DEFAULT_VIX_PATH = Path(__file__).resolve().parents[1] / "runs/sleeve-f-calendar-vix/india_vix_clean.csv"


def stitch_oos_daily(fold_artifacts: Mapping[Any, Any] | Iterable[Any]) -> pd.DataFrame:
    """Stitch fold daily frames in date order and reject any overlap."""
    items = list(fold_artifacts.items()) if isinstance(fold_artifacts, Mapping) else list(enumerate(fold_artifacts))
    frames: list[pd.DataFrame] = []
    for fold, artifact in items:
        daily = _daily_frame(artifact)
        if daily.empty:
            continue
        frame = daily.copy()
        frame["trade_date"] = _date_column(frame)
        if "oos_start" in _mapping(artifact):
            assert frame["trade_date"].min() >= pd.Timestamp(_mapping(artifact)["oos_start"]).date()
        if "oos_end" in _mapping(artifact):
            assert frame["trade_date"].max() <= pd.Timestamp(_mapping(artifact)["oos_end"]).date()
        frame["fold"] = str(fold)
        frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["policy_return_bps", "trade_count", "fold"])
    result = pd.concat(frames, ignore_index=True, sort=False)
    assert not result["trade_date"].duplicated().any(), "strictly-OOS fold daily series overlap"
    if "policy_return_bps" not in result:
        raise ValueError("daily artifact needs policy_return_bps")
    return result.sort_values("trade_date", kind="stable").set_index("trade_date")


stitch_daily_series = stitch_oos_daily


def annualized_sharpe(returns: Iterable[float]) -> float:
    """Annualized daily Sharpe using the registered 252-session factor."""
    return sharpe([float(value) for value in returns])


def data_hash_seed(values: Iterable[Any], *, label: str = "") -> int:
    """Derive a stable uint32 seed from data, never from wall-clock state."""
    canonical = "|".join(f"{value!r}" for value in values)
    return int.from_bytes(sha256(f"{label}|{canonical}".encode("utf-8")).digest()[:4], "big")


def mbb_ci(
    returns: Iterable[float],
    *,
    seed: int | None = None,
    statistic: Any = None,
) -> tuple[float, float]:
    """Return the frozen 20-day, 10,000-draw, 95% MBB interval."""
    values = [float(value) for value in returns]
    return moving_block_bootstrap_ci(
        values,
        block_size=MBB_BLOCK_SIZE,
        samples=MBB_DRAWS,
        confidence=MBB_CONFIDENCE,
        seed=data_hash_seed(values, label="c1-mbb") if seed is None else int(seed),
        statistic=statistic,
    )


def paired_difference_mbb(
    candidate_returns: Iterable[float],
    baseline_returns: Iterable[float],
    *,
    seed: int | None = None,
) -> tuple[float, float]:
    """Return the paired MBB CI for candidate minus baseline mean PnL."""
    candidate = [float(value) for value in candidate_returns]
    baseline = [float(value) for value in baseline_returns]
    return paired_moving_block_bootstrap_ci(
        candidate,
        baseline,
        block_size=MBB_BLOCK_SIZE,
        samples=MBB_DRAWS,
        confidence=MBB_CONFIDENCE,
        seed=data_hash_seed(candidate + baseline, label="c1-paired-mbb") if seed is None else int(seed),
    )


def score_quintile_monotonicity(trades: pd.DataFrame) -> dict[str, Any]:
    """Summarize whether realized trade PnL rises across score quintiles."""
    if not isinstance(trades, pd.DataFrame) or trades.empty or "score" not in trades:
        return {"means": [], "counts": [], "monotonic": False, "slope": 0.0}
    returns = _trade_returns(trades)
    frame = pd.DataFrame({"score": pd.to_numeric(trades["score"], errors="coerce"), "return": returns}).dropna()
    if frame.empty:
        return {"means": [], "counts": [], "monotonic": False, "slope": 0.0}
    frame = frame.sort_values(["score"], kind="stable").reset_index(drop=True)
    groups = [group for group in _quintile_groups(len(frame))]
    means = [float(frame.iloc[indexes]["return"].mean()) for indexes in groups]
    counts = [len(indexes) for indexes in groups]
    monotonic = len(means) > 1 and all(left <= right for left, right in zip(means, means[1:]))
    slope = (means[-1] - means[0]) / (len(means) - 1) if len(means) > 1 else 0.0
    return {"means": means, "counts": counts, "monotonic": monotonic, "slope": float(slope)}


def quarterly_pnl_concentration(daily: pd.DataFrame) -> dict[str, Any]:
    """Report quarterly totals and concentration without a made-up gate cap."""
    frame = _normalise_daily(daily)
    totals = frame.groupby(frame["trade_date"].map(lambda day: str(pd.Timestamp(day).to_period("Q"))))[
        "policy_return_bps"
    ].sum()
    absolute_total = float(totals.abs().sum())
    positive_total = float(totals[totals > 0].sum())
    shares = {
        str(period): (abs(float(value)) / absolute_total if absolute_total else 0.0)
        for period, value in totals.items()
    }
    positive_shares = {
        str(period): (float(value) / positive_total if positive_total else 0.0)
        for period, value in totals.items()
        if value > 0
    }
    return {
        "quarterly_pnl_bps": {str(period): float(value) for period, value in totals.items()},
        "absolute_pnl_shares": shares,
        "positive_pnl_shares": positive_shares,
        "max_absolute_share": max(shares.values(), default=0.0),
        "max_positive_share": max(positive_shares.values(), default=0.0),
        "gate_status": "REPORT-ONLY",
    }


def stability_slices(
    daily: pd.DataFrame,
    trades: pd.DataFrame | None = None,
    *,
    vix: pd.DataFrame | Mapping[Any, float] | None = None,
    vix_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return registered stability slices; VIX output is diagnostic only."""
    trade_frame = _annotate_trades(trades) if isinstance(trades, pd.DataFrame) and not trades.empty else pd.DataFrame()
    if not trade_frame.empty:
        result = {
            "vendor_seam": _group_trades(trade_frame, lambda row: _vendor_bucket(row["trade_date"])),
            "lot_eras": _group_trades(trade_frame, lambda row: _era_label(row["trade_date"], "lot")),
            "tax_eras": _group_trades(trade_frame, lambda row: _era_label(row["trade_date"], "tax")),
            "expiry_vs_non_expiry_weeks": _group_trades(trade_frame, lambda row: "expiry" if row["expiry_week"] else "non_expiry"),
            "time_buckets": _group_trades(trade_frame, lambda row: row["time_bucket"]),
            "late_entry": _group_trades(trade_frame, lambda row: row["late_entry"]),
        }
    else:
        result = {}
    vix_frame = _load_vix(vix, vix_path)
    if not vix_frame.empty:
        annotated = _normalise_daily(daily).merge(vix_frame, on="trade_date", how="left")
        result["lagged_vix_terciles"] = {
            **_group_daily(annotated, "vix_tercile"),
            "diagnostic_only": True,
        }
    else:
        result["lagged_vix_terciles"] = {"diagnostic_only": True, "available": False}
    for name in ("vendor_seam", "lot_eras", "tax_eras", "expiry_vs_non_expiry_weeks", "time_buckets", "late_entry"):
        result.setdefault(name, {})["gate_status"] = "REPORT-ONLY"
    return result


def compute_metrics(
    artifact: Any,
    *,
    trades: pd.DataFrame | None = None,
    vix: pd.DataFrame | Mapping[Any, float] | None = None,
    vix_path: str | Path | None = None,
    dsr_k: int = DSR_K,
) -> dict[str, Any]:
    """Calculate all M8 metrics from replay artifacts."""
    daily = stitch_oos_daily(artifact) if isinstance(artifact, Mapping) and not _looks_like_daily(artifact) else _normalise_daily(_daily_frame(artifact))
    if trades is None:
        trades = _trades_from_artifact(artifact)
    values = [float(value) for value in daily["policy_return_bps"]]
    seed = data_hash_seed(values, label="c1-sharpe-mbb")
    sharpe_interval = mbb_ci(values, seed=seed, statistic=annualized_sharpe)
    mean_interval = mbb_ci(values, seed=seed, statistic=None)
    return {
        "daily_count": len(daily),
        "trade_days": int((daily.get("trade_count", pd.Series(0, index=daily.index)) > 0).sum()),
        "total_pnl_bps": float(sum(values)),
        "mean_daily_pnl_bps": float(sum(values) / len(values)) if values else 0.0,
        "annualized_sharpe": annualized_sharpe(values),
        "sharpe_mbb_20d_10k_ci95": {"lower": float(sharpe_interval[0]), "upper": float(sharpe_interval[1])},
        "mean_daily_pnl_mbb_20d_10k_ci95": {"lower": float(mean_interval[0]), "upper": float(mean_interval[1])},
        "score_quintile_monotonicity": score_quintile_monotonicity(trades) if trades is not None else {},
        "quarterly_pnl_concentration": quarterly_pnl_concentration(daily),
        "slices": stability_slices(daily, trades, vix=vix, vix_path=vix_path),
        "dsr": deflated_sharpe_ratio(values, trials=int(dsr_k)),
        "dsr_k": int(dsr_k),
        "seed_policy": "SHA-256 of ordered data values and metric label; first four digest bytes as uint32",
    }


def paired_metrics(candidate: Any, baseline: Any, *, seed: int | None = None) -> dict[str, Any]:
    """Align two stitched daily artifacts and calculate paired 6b evidence."""
    left = _normalise_daily(stitch_oos_daily(candidate) if isinstance(candidate, Mapping) and not _looks_like_daily(candidate) else _daily_frame(candidate))
    right = _normalise_daily(stitch_oos_daily(baseline) if isinstance(baseline, Mapping) and not _looks_like_daily(baseline) else _daily_frame(baseline))
    joined = left[["policy_return_bps"]].join(right[["policy_return_bps"]], lsuffix="_candidate", rsuffix="_baseline", how="inner")
    if len(joined) != len(left) or len(joined) != len(right):
        raise AssertionError("paired daily artifacts must cover the same dates")
    candidate_values = joined["policy_return_bps_candidate"].tolist()
    baseline_values = joined["policy_return_bps_baseline"].tolist()
    low, high = paired_difference_mbb(candidate_values, baseline_values, seed=seed)
    return {
        "candidate_sharpe": annualized_sharpe(candidate_values),
        "baseline_sharpe": annualized_sharpe(baseline_values),
        "delta_sharpe": annualized_sharpe(candidate_values) - annualized_sharpe(baseline_values),
        "paired_mbb_ci_low": float(low),
        "paired_mbb_ci_high": float(high),
        "mean_daily_pnl_diff": float(sum(a - b for a, b in zip(candidate_values, baseline_values)) / len(candidate_values)) if candidate_values else 0.0,
    }


def _daily_frame(artifact: Any) -> pd.DataFrame:
    if isinstance(artifact, ReplayResult):
        return artifact.daily.copy()
    if hasattr(artifact, "daily"):
        return artifact.daily.copy()
    if isinstance(artifact, Mapping):
        if "daily" in artifact:
            return _daily_frame(artifact["daily"])
        if "replay_result" in artifact:
            return _daily_frame(artifact["replay_result"])
    if isinstance(artifact, pd.DataFrame):
        return artifact.copy()
    raise TypeError("artifact does not contain a daily DataFrame")


def _looks_like_daily(artifact: Mapping[Any, Any]) -> bool:
    return "daily" in artifact or "policy_return_bps" in artifact


def _mapping(artifact: Any) -> Mapping[str, Any]:
    return artifact if isinstance(artifact, Mapping) else {}


def _normalise_daily(daily: pd.DataFrame) -> pd.DataFrame:
    frame = daily.copy()
    if "trade_date" in frame:
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise").dt.date
    else:
        frame = frame.reset_index().rename(columns={frame.index.name or "index": "trade_date"})
        frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="raise").dt.date
    if "policy_return_bps" not in frame:
        raise ValueError("daily artifact needs policy_return_bps")
    assert not frame["trade_date"].duplicated().any(), "daily series overlaps dates"
    return frame.sort_values("trade_date", kind="stable").reset_index(drop=True)


def _date_column(frame: pd.DataFrame) -> pd.Series:
    if "trade_date" in frame:
        return pd.to_datetime(frame["trade_date"], errors="raise").dt.date
    if isinstance(frame.index, pd.DatetimeIndex):
        return pd.Series(frame.index.date, index=frame.index)
    return pd.to_datetime(frame.index, errors="raise").date


def _trades_from_artifact(artifact: Any) -> pd.DataFrame | None:
    if isinstance(artifact, ReplayResult):
        return artifact.trades
    if hasattr(artifact, "trades"):
        return artifact.trades
    if isinstance(artifact, Mapping):
        if isinstance(artifact.get("trades"), pd.DataFrame):
            return artifact["trades"]
        for value in artifact.values():
            found = _trades_from_artifact(value)
            if found is not None:
                return found
    return None


def _trade_returns(trades: pd.DataFrame) -> pd.Series:
    for column in ("net_bps", "realized_return_bps", "policy_return_bps", "pnl_bps", "realized_pnl", "pnl"):
        if column in trades:
            return pd.to_numeric(trades[column], errors="coerce")
    return pd.Series([0.0] * len(trades), index=trades.index)


def _quintile_groups(size: int) -> list[list[int]]:
    count = min(5, size)
    base, remainder = divmod(size, count)
    groups: list[list[int]] = []
    cursor = 0
    for group_number in range(count):
        width = base + (1 if group_number < remainder else 0)
        groups.append(list(range(cursor, cursor + width)))
        cursor += width
    return groups


def _annotate_trades(trades: pd.DataFrame) -> pd.DataFrame:
    frame = trades.copy()
    frame["trade_date"] = pd.to_datetime(frame.get("trade_date", frame.get("entry_datetime")), errors="raise").dt.date
    frame["pnl_bps"] = _trade_returns(frame)
    if "front_expiry" in frame:
        expiry = pd.to_datetime(frame["front_expiry"], errors="coerce").dt.date
        frame["expiry_week"] = [
            bool(pd.notna(expiry_day) and (expiry_day - day).days <= 4)
            for expiry_day, day in zip(expiry, frame["trade_date"])
        ]
    else:
        frame["expiry_week"] = False
    if "decision_datetime" in frame:
        decision = pd.to_datetime(frame["decision_datetime"], errors="raise")
        frame["time_bucket"] = decision.map(_time_bucket)
        frame["late_entry"] = decision.dt.strftime("%H:%M").map(lambda value: "<=14:29" if value <= "14:29" else ">=14:30")
    else:
        frame["time_bucket"] = "unknown"
        frame["late_entry"] = "unknown"
    return frame


def _group_trades(frame: pd.DataFrame, bucket: Any) -> dict[str, Any]:
    groups: dict[str, dict[str, Any]] = {}
    for index, row in frame.iterrows():
        name = str(bucket(row))
        entry = groups.setdefault(name, {"trades": 0, "pnl_bps": 0.0})
        entry["trades"] += 1
        entry["pnl_bps"] += float(row["pnl_bps"])
    for entry in groups.values():
        entry["mean_pnl_bps"] = entry["pnl_bps"] / entry["trades"] if entry["trades"] else 0.0
    return groups


def _group_daily(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, group in frame.dropna(subset=[column]).groupby(column, sort=True):
        values = pd.to_numeric(group["policy_return_bps"], errors="coerce")
        result[str(name)] = {"days": len(group), "pnl_bps": float(values.sum()), "mean_pnl_bps": float(values.mean())}
    return result


def _vendor_bucket(day: Any) -> str:
    value = pd.Timestamp(day).date()
    if value < pd.Timestamp("2024-10-01").date():
        return "pre_2024_10"
    if value <= pd.Timestamp("2024-11-30").date():
        return "seam_2024_10_11"
    return "post_2024_11"


def _era_label(day: Any, kind: str) -> str:
    value = pd.Timestamp(day).date()
    matching = [era for era in ERA_TABLE if pd.Timestamp(str(era["effective_date"])).date() <= value]
    if not matching:
        return "unknown"
    era = matching[-1]
    if kind == "lot":
        return f"effective_{era['effective_date']}_lot_{era['lot_size']}"
    return f"effective_{era['effective_date']}_stt_{era['stt_sell_pct']}_txn_{era['nse_txn_per_lakh_sell']}"


def _time_bucket(value: pd.Timestamp) -> str:
    minute = value.hour * 60 + value.minute
    if minute < 660:
        return "09:45-10:59"
    if minute < 810:
        return "12:00-13:29"
    if minute < 870:
        return "13:30-14:29"
    return "14:30-14:54"


def _load_vix(vix: Any, vix_path: str | Path | None) -> pd.DataFrame:
    source = vix
    if source is None and vix_path is not None:
        source = pd.read_csv(vix_path)
    if source is None:
        return pd.DataFrame()
    if isinstance(source, Mapping):
        source = pd.DataFrame({"trade_date": list(source.keys()), "vix": list(source.values())})
    frame = source.copy()
    date_column = "trade_date" if "trade_date" in frame else "date"
    value_column = "vix" if "vix" in frame else "close" if "close" in frame else "vix_close"
    frame["source_date"] = pd.to_datetime(frame[date_column], errors="raise").dt.date
    frame["vix"] = pd.to_numeric(frame[value_column], errors="coerce")
    frame = frame[["source_date", "vix"]].dropna().sort_values("source_date")
    days = pd.DataFrame({"trade_date": frame["source_date"].unique()})
    joined = days.merge(frame, left_on="trade_date", right_on="source_date", how="left").drop(columns="source_date")
    joined["lagged_vix"] = joined["vix"].shift(1)
    valid = joined["lagged_vix"].dropna()
    if valid.empty:
        return pd.DataFrame(columns=["trade_date", "vix_tercile"])
    ranks = valid.rank(method="first")
    terciles = pd.qcut(ranks, q=3, labels=["low", "mid", "high"], duplicates="drop")
    joined["vix_tercile"] = pd.Series(terciles.astype(str).values, index=valid.index)
    return joined[["trade_date", "vix_tercile"]]
