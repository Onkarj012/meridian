"""Causal five-minute decisions and executable minute-bar label artifacts.

This module intentionally keeps feature construction separate from forward
label simulation: a decision is made only after a completed five-minute bar,
then execution starts at the following *minute* open.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, time
from pathlib import Path
from statistics import pstdev
from typing import Any, Iterable

import polars as pl

def enrich_graph_features(rows, *args, **kwargs):
    """Graph features are deferred in MERIDIAN; return neutral rows."""
    return rows

GOLD_FORMAT = "meridian.causal-gold.v1"
PRICE_FEATURE_VERSION = "causal-five-minute-v3"
FEATURE_VERSION_BY_SET = {
    "price_only": PRICE_FEATURE_VERSION,
    "sector": "causal-five-minute-v3-sector-v1",
    "graph": "causal-five-minute-v3-graph-v1",
    "graph_sector": "causal-five-minute-v3-sector-graph-v1",
}
FEATURE_VERSION = FEATURE_VERSION_BY_SET["graph_sector"]
LABEL_VERSION = "minute-executable-long-short-v1"
GOLD_START, GOLD_END = "2020-01-01", "2025-12-31"
SESSION_CLOSE = time(15, 29)
DEFAULT_GRID = ((0.5, 0.5, 15), (0.75, 0.5, 30), (1.0, 0.75, 30))


def build_gold(
    lake_root: Path,
    universe_manifest: Path,
    output: Path,
    training_end: str,
    *,
    cost_bps: float = 8.0,
    grid: Iterable[tuple[float, float, int]] = DEFAULT_GRID,
    feature_set: str = "graph_sector",
    fixed_label_contract: dict[str, object] | None = None,
) -> dict[str, object]:
    """Build one immutable causal gold artifact from a frozen lake/universe.

    ``training_end`` is an ISO trading date.  The ATR contract is selected
    exclusively from decisions on or before that date, then used unchanged for
    every later row in this artifact.
    """
    lake_root, universe_manifest, output = Path(lake_root), Path(universe_manifest), Path(output)
    universe = json.loads(universe_manifest.read_text(encoding="utf-8"))
    symbols = sorted({str(item["symbol"]).upper() for item in universe["symbols"]})
    sectors, sector_indices = _universe_signal_metadata(universe)
    source_identity = _lake_identity(lake_root)
    candidates = tuple((float(t), float(s), int(h)) for t, s, h in grid)
    if not candidates:
        raise ValueError("label ATR grid must contain at least one contract")
    feature_version = _feature_version(feature_set)
    _require_sector_metadata(symbols, sectors, feature_set)
    output.parent.mkdir(parents=True, exist_ok=True)
    minutes = _load_minutes(lake_root, set(symbols))
    decisions = causal_five_minute_features(minutes, set(symbols), sectors, sector_indices, feature_set=feature_set)
    selected = _normalize_fixed_label_contract(fixed_label_contract) if fixed_label_contract is not None else _select_label_contract_for_decisions(decisions, training_end, candidates, cost_bps)
    rows_written, ambiguity_count = _write_labeled_gold_parts(decisions, output, selected, cost_bps)
    label_identity = {"version": LABEL_VERSION, "contract": selected, "selection_cutoff": training_end}
    cost_identity = {"version": "generic-all-in-friction-v1", "applied_once_per_completed_trade": True, "base_cost_bps": cost_bps, "sensitivity_bps": [8, 12, 16, 20]}
    identity = {"format": GOLD_FORMAT, "source": source_identity, "universe_manifest_id": universe["manifest_id"], "study_rows": {"start": GOLD_START, "end": GOLD_END, "future_rows_reserved_from": "2026-01-01"}, "feature": feature_version, "feature_set": feature_set, "feature_sets_available": sorted(FEATURE_VERSION_BY_SET), "label": label_identity, "cost": cost_identity}
    manifest = {**identity, "gold_id": _digest(identity), "rows": rows_written, "ambiguous_rows": ambiguity_count, "output": str(output), "universe_manifest": str(universe_manifest)}
    manifest_path = output.with_suffix(".manifest.json")
    _write_json(manifest_path, manifest)
    contract_path = output.with_suffix(".label-contract.json")
    _write_json(contract_path, {"format": "meridian.label-contract.v1", "contract_id": _digest(label_identity), **label_identity, "candidates": [dict(target_atr=t, stop_atr=s, timeout_minutes=h) for t, s, h in candidates]})
    return {"rows_written": rows_written, "ambiguous_rows": ambiguity_count, "output": str(output), "manifest": str(manifest_path), "label_contract": str(contract_path), "gold_id": manifest["gold_id"], "feature_set": feature_set, "feature": feature_version}


def _select_label_contract_for_decisions(decisions: list[dict[str, Any]], training_end: str, grid: tuple[tuple[float, float, int], ...], cost_bps: float) -> dict[str, object]:
    ranked: dict[tuple[float, float, int], list[float]] = {candidate: [0.0, 0.0] for candidate in grid}
    train = [row for row in decisions if str(row["timestamp"])[:10] <= training_end]
    for candidate in grid:
        labeled, _ = label_decisions(train, {"target_atr": candidate[0], "stop_atr": candidate[1], "timeout_minutes": candidate[2]}, cost_bps)
        usable = [float(row["long_net_return_bps"]) + float(row["short_net_return_bps"]) for row in labeled if not row["label_excluded"]]
        ranked[candidate][0] += sum(usable)
        ranked[candidate][1] += len(usable)
    if not any(count for _, count in ranked.values()):
        raise ValueError("no completed feature decisions in training period")
    scores = []
    for (target, stop, timeout), (total, count) in ranked.items():
        scores.append((total / count if count else float("-inf"), target, stop, timeout))
    _, target, stop, timeout = max(scores, key=lambda item: (item[0], -item[1], -item[2], -item[3]))
    return {"target_atr": target, "stop_atr": stop, "timeout_minutes": timeout, "selection": "training_mean_long_short_net_bps"}


def _normalize_fixed_label_contract(contract: dict[str, object]) -> dict[str, object]:
    required = ("target_atr", "stop_atr", "timeout_minutes")
    missing = [key for key in required if key not in contract]
    if missing:
        raise ValueError(f"fixed label contract is missing: {', '.join(missing)}")
    target, stop, timeout = float(contract["target_atr"]), float(contract["stop_atr"]), int(contract["timeout_minutes"])
    if target <= 0 or stop <= 0 or timeout <= 0:
        raise ValueError("fixed label contract target_atr, stop_atr, and timeout_minutes must be positive")
    return {"target_atr": target, "stop_atr": stop, "timeout_minutes": timeout, "selection": "fixed_preselected_contract"}


def _write_labeled_gold_parts(decisions: list[dict[str, Any]], output: Path, selected: dict[str, object], cost_bps: float) -> tuple[int, int]:
    temporary = output.with_suffix(output.suffix + ".parts.tmp")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    rows_written = ambiguity_count = part_count = 0
    try:
        by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for decision in decisions:
            by_symbol[str(decision["symbol"]).upper()].append(decision)
        for symbol in sorted(by_symbol):
            rows, ambiguities = label_decisions(by_symbol[symbol], selected, cost_bps)
            if not rows:
                continue
            pl.DataFrame(rows).write_parquet(temporary / f"part-{part_count:05d}-{symbol}.parquet", compression="zstd")
            rows_written += len(rows)
            ambiguity_count += ambiguities
            part_count += 1
        if part_count == 0:
            raise ValueError("no completed feature decisions in gold study window")
        pl.scan_parquet(str(temporary / "*.parquet")).sink_parquet(output, compression="zstd")
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return rows_written, ambiguity_count


def causal_five_minute_features(
    minutes: list[dict[str, Any]],
    symbols: set[str] | None = None,
    sector_by_symbol: dict[str, str] | None = None,
    sector_index_by_sector: dict[str, str] | None = None,
    *,
    feature_set: str = "price_only",
) -> list[dict[str, Any]]:
    """Return completed-bar decisions.  No future row is read for any feature."""
    feature_version = _feature_version(feature_set)
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in minutes:
        symbol = str(row["symbol"]).upper()
        if symbols is None or symbol in symbols:
            by_symbol[symbol].append(row)
    output: list[dict[str, Any]] = []
    for symbol, series in by_symbol.items():
        series.sort(key=lambda item: item["timestamp"])
        bars: list[list[dict[str, Any]]] = []
        bucket: list[dict[str, Any]] = []
        bucket_key: tuple[str, int] | None = None
        for row in series:
            ts = _row_dt(row)
            key = (ts.date().isoformat(), ((ts.hour * 60 + ts.minute) - 555) // 5)
            if bucket_key is not None and key != bucket_key:
                bars.append(bucket)
                bucket = []
            bucket_key = key
            bucket.append(row)
        if bucket:
            bars.append(bucket)
        closes: list[float] = []
        ranges: list[float] = []
        volumes: list[float] = []
        current_day = None
        cumulative_pv = 0.0
        cumulative_volume = 0.0
        day_open = 0.0
        for bar in bars:
            # Partial bars are never feature decisions; their final minute must
            # close at x:x4 for a complete 5-minute 09:15-aligned interval.
            end = _row_dt(bar[-1])
            if len(bar) != 5 or (end.hour * 60 + end.minute - 555) % 5 != 4:
                continue
            if current_day != end.date():
                current_day = end.date()
                cumulative_pv = 0.0
                cumulative_volume = 0.0
                day_open = float(bar[0]["open"])
            close = float(bar[-1]["close"])
            high, low = max(float(x["high"]) for x in bar), min(float(x["low"]) for x in bar)
            open_ = float(bar[0]["open"])
            volume = sum(float(x["volume"]) for x in bar)
            typical = (high + low + close) / 3.0
            cumulative_pv += typical * volume
            cumulative_volume += volume
            vwap = cumulative_pv / cumulative_volume if cumulative_volume else close
            closes.append(close); ranges.append(high - low); volumes.append(volume)
            prior = closes[-2] if len(closes) > 1 else close
            base_3 = closes[-4] if len(closes) >= 4 else closes[0]
            base_6 = closes[-7] if len(closes) >= 7 else closes[0]
            base_12 = closes[-13] if len(closes) >= 13 else closes[0]
            base_24 = closes[-25] if len(closes) >= 25 else closes[0]
            base_48 = closes[-49] if len(closes) >= 49 else closes[0]
            atr = sum(ranges[-14:]) / len(ranges[-14:])
            return_1 = (close - prior) / prior if prior else 0.0
            rolling_returns = [
                (closes[j] - closes[j - 1]) / closes[j - 1]
                for j in range(max(1, len(closes) - 20), len(closes))
                if closes[j - 1]
            ]
            rolling_vol = pstdev(rolling_returns) if len(rolling_returns) > 1 else 0.0
            avg_volume = sum(volumes[-20:]) / len(volumes[-20:])
            minutes_since_open = end.hour * 60 + end.minute - 555
            phase = "open" if minutes_since_open < 45 else "close" if minutes_since_open >= 315 else "mid"
            output.append({
                "symbol": symbol, "timestamp": end.isoformat(), "decision_timestamp": end.isoformat(),
                "open": open_, "high": high, "low": low, "close": close, "volume": volume,
                "return_1": return_1,
                "return_3": (close - base_3) / base_3 if base_3 else 0.0,
                "return_6": (close - base_6) / base_6 if base_6 else 0.0,
                "return_12": (close - base_12) / base_12 if base_12 else 0.0,
                "return_24": (close - base_24) / base_24 if base_24 else 0.0,
                "return_48": (close - base_48) / base_48 if base_48 else 0.0,
                "vwap": vwap,
                "vwap_distance": (close - vwap) / vwap if vwap else 0.0,
                "volume_ratio": volume / avg_volume if avg_volume else 1.0,
                "volume_shock": volume / avg_volume if avg_volume else 1.0,
                "atr": atr,
                "atr_bps": atr / close * 10000 if close else 0.0,
                "rolling_volatility": rolling_vol,
                "intraday_range_position": (close - low) / (high - low) if high > low else 0.5,
                "opening_gap": (open_ - prior) / prior if prior and minutes_since_open == 4 else 0.0,
                "open_to_now_return": (close - day_open) / day_open if day_open else 0.0,
                # Volatility-normalized own return: comparable across symbols of
                # very different price/vol scales. This is a within-symbol
                # standardized momentum, not a market-relative feature; true
                # cross-sectional relative strength awaits a per-bar universe
                # build (see PRD fail-closed stance on absent feeds).
                "return_1_vol_norm": _vol_normalized(return_1, rolling_vol),
                "relative_strength": _vol_normalized(return_1, rolling_vol),
                "session_phase": phase,
                "feature_version": feature_version,
            })
    output = sorted(output, key=lambda row: (row["timestamp"], row["symbol"]))
    output = _apply_cross_sectional_signal_features(output, sector_by_symbol or {}, sector_index_by_sector or {}, feature_set)
    return sorted(output, key=lambda row: (row["symbol"], row["timestamp"]))


def select_label_contract(decisions: list[dict[str, Any]], training_end: str, grid: Iterable[tuple[float, float, int]], cost_bps: float) -> dict[str, object]:
    """Pick a deterministic ATR contract using training rows only."""
    train = [row for row in decisions if str(row["timestamp"])[:10] <= training_end]
    if not train:
        raise ValueError("no completed feature decisions in training period")
    ranked = []
    for target, stop, timeout in grid:
        labeled, _ = label_decisions(train, {"target_atr": target, "stop_atr": stop, "timeout_minutes": timeout}, cost_bps)
        usable = [float(row["long_net_return_bps"]) + float(row["short_net_return_bps"]) for row in labeled if not row["label_excluded"]]
        ranked.append((sum(usable) / len(usable) if usable else float("-inf"), target, stop, timeout))
    _, target, stop, timeout = max(ranked, key=lambda item: (item[0], -item[1], -item[2], -item[3]))
    return {"target_atr": target, "stop_atr": stop, "timeout_minutes": timeout, "selection": "training_mean_long_short_net_bps"}


def label_decisions(decisions: list[dict[str, Any]], contract: dict[str, object], cost_bps: float, minutes: list[dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], int]:
    """Label decisions independently for both sides with minute chronology.

    Feature rows contain their source minute bars in normal lake use.  Supplying
    ``minutes`` is supported for callers building features and labels separately.
    """
    # Build minute lookup from the module-local attachment made by build_gold.
    minute_map, timestamp_map = (_MINUTE_CONTEXT, _MINUTE_TS_CONTEXT) if minutes is None else _minute_context(minutes)
    out, ambiguities = [], 0
    for row in decisions:
        key = (str(row["symbol"]).upper(), str(row["timestamp"])[:10])
        series = minute_map.get(key, [])
        result = _label_one(row, series, timestamp_map.get(key, []), contract, cost_bps)
        ambiguities += int(result["ambiguous_bar"])
        out.append({**row, **result})
    return out, ambiguities


def reprice_gross_outcomes(rows: Iterable[dict[str, Any]], cost_bps: float) -> list[dict[str, Any]]:
    """Reprice preserved gross returns; labels and barriers are not regenerated."""
    return [{**row, "cost_bps": cost_bps, "long_net_return_bps": float(row["long_gross_return_bps"]) - cost_bps, "short_net_return_bps": float(row["short_gross_return_bps"]) - cost_bps} for row in rows]


_MINUTE_CONTEXT: dict[tuple[str, str], list[dict[str, Any]]] = {}
_MINUTE_TS_CONTEXT: dict[tuple[str, str], list[datetime]] = {}


def _label_one(row: dict[str, Any], series: list[dict[str, Any]], timestamps: list[datetime], contract: dict[str, object], cost_bps: float) -> dict[str, object]:
    decision = _dt(row["timestamp"])
    entry_index = bisect_right(timestamps, decision)
    if entry_index >= len(series):
        return _excluded("no_next_minute_open")
    entry_row = series[entry_index]
    entry = float(entry_row["open"])
    if entry <= 0 or timestamps[entry_index].date() != decision.date():
        return _excluded("no_next_minute_open")
    horizon = int(contract["timeout_minutes"])
    future = [item for item, ts in zip(series[entry_index:entry_index + horizon], timestamps[entry_index:entry_index + horizon]) if ts.time() <= SESSION_CLOSE]
    if not future:
        return _excluded("no_session_minutes")
    atr = max(float(row["atr"]), entry * 0.0001)
    target_mult, stop_mult = float(contract["target_atr"]), float(contract["stop_atr"])
    target_gross = target_mult * atr / entry * 10000
    stop_gross = stop_mult * atr / entry * 10000
    target_net = target_gross - cost_bps
    stop_net_loss = stop_gross + cost_bps
    breakeven = stop_net_loss / (target_net + stop_net_loss) if target_net + stop_net_loss > 0 else 1.0
    long = _simulate_minute(entry, atr, future, target_mult, stop_mult, "long", horizon)
    short = _simulate_minute(entry, atr, future, target_mult, stop_mult, "short", horizon)
    ambiguous = bool(long["ambiguous"] or short["ambiguous"])
    return {"entry_timestamp": str(entry_row["timestamp"]), "entry_price": entry, "label_excluded": False, "ambiguous_bar": ambiguous, "long_label": long["label"], "short_label": short["label"], "long_exit_reason": long["reason"], "short_exit_reason": short["reason"], "long_exit_bars": long["bars"], "short_exit_bars": short["bars"], "long_gross_return_bps": long["gross"], "short_gross_return_bps": short["gross"], "long_net_return_bps": long["gross"] - cost_bps, "short_net_return_bps": short["gross"] - cost_bps, "long_target_net_bps": target_net, "short_target_net_bps": target_net, "long_stop_net_loss_bps": stop_net_loss, "short_stop_net_loss_bps": stop_net_loss, "breakeven_probability": breakeven, "cost_bps": cost_bps, "target_class": "LONG" if long["label"] == "LONG_SUCCESS" else "SHORT" if short["label"] == "SHORT_SUCCESS" else "NO_TRADE"}


def _simulate_minute(entry: float, atr: float, future: list[dict[str, Any]], target_mult: float, stop_mult: float, side: str, horizon: int) -> dict[str, object]:
    target = entry + target_mult * atr if side == "long" else entry - target_mult * atr
    stop = entry - stop_mult * atr if side == "long" else entry + stop_mult * atr
    for bars, item in enumerate(future, 1):
        high, low = float(item["high"]), float(item["low"])
        hit_target = high >= target if side == "long" else low <= target
        hit_stop = low <= stop if side == "long" else high >= stop
        if hit_stop:  # stop-first when both are reachable inside this minute
            gross = ((stop - entry) if side == "long" else (entry - stop)) / entry * 10000
            return {"label": f"{side.upper()}_FAIL", "reason": "stop", "bars": bars, "gross": gross, "ambiguous": hit_target}
        if hit_target:
            gross = ((target - entry) if side == "long" else (entry - target)) / entry * 10000
            return {"label": f"{side.upper()}_SUCCESS", "reason": "target", "bars": bars, "gross": gross, "ambiguous": False}
    exit_row = future[-1]
    forced = _dt(exit_row["timestamp"]).time() == SESSION_CLOSE and len(future) < horizon
    exit_price = float(exit_row["close"])
    gross = ((exit_price - entry) if side == "long" else (entry - exit_price)) / entry * 10000
    return {"label": "NO_TRADE", "reason": "forced_exit" if forced else "timeout", "bars": len(future), "gross": gross, "ambiguous": False}


def _excluded(reason: str) -> dict[str, object]:
    return {"label_excluded": True, "ambiguous_bar": False, "long_label": "NO_TRADE", "short_label": "NO_TRADE", "long_exit_reason": reason, "short_exit_reason": reason, "long_exit_bars": 0, "short_exit_bars": 0, "long_gross_return_bps": 0.0, "short_gross_return_bps": 0.0, "long_net_return_bps": 0.0, "short_net_return_bps": 0.0, "cost_bps": 0.0, "target_class": "NO_TRADE"}


def _feature_version(feature_set: str) -> str:
    try:
        return FEATURE_VERSION_BY_SET[feature_set]
    except KeyError as exc:
        allowed = ", ".join(sorted(FEATURE_VERSION_BY_SET))
        raise ValueError(f"unknown gold feature_set={feature_set!r}; expected one of: {allowed}") from exc


def _universe_signal_metadata(universe: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    sectors: dict[str, str] = {}
    sector_indices: dict[str, str] = {}
    for item in universe.get("symbols", []):
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol", "")).upper()
        sector = str(item.get("sector") or "")
        index = str(item.get("sector_index") or "NIFTY 50")
        if symbol and sector:
            sectors[symbol] = sector
        if sector:
            sector_indices[sector] = index
    return sectors, sector_indices


def _require_sector_metadata(symbols: list[str], sector_by_symbol: dict[str, str], feature_set: str) -> None:
    if feature_set not in {"sector", "graph_sector"}:
        return
    missing = [symbol for symbol in symbols if not sector_by_symbol.get(symbol)]
    if missing:
        raise ValueError(f"gold feature_set={feature_set!r} requires universe sector metadata for: {', '.join(missing)}")


def _apply_cross_sectional_signal_features(
    rows: list[dict[str, Any]],
    sector_by_symbol: dict[str, str],
    sector_index_by_sector: dict[str, str],
    feature_set: str,
) -> list[dict[str, Any]]:
    _feature_version(feature_set)
    if feature_set == "price_only":
        return [_with_neutral_signal_features(row, keep_sector=False, keep_graph=False) for row in rows]
    if feature_set == "sector":
        return [_with_neutral_signal_features(row, keep_sector=True, keep_graph=False) for row in _sector_signal_features(rows, sector_by_symbol, sector_index_by_sector)]

    symbols = {str(row["symbol"]) for row in rows}
    enriched = enrich_graph_features(
        rows,
        {symbol: sector_by_symbol.get(symbol, "UNKNOWN") for symbol in symbols},
        sector_index_by_sector,
        correlation_window=48,
        min_history=12,
        top_k=5,
        edge_refresh_bars=12,
    )
    keep_sector = feature_set in {"sector", "graph_sector"}
    keep_graph = feature_set in {"graph", "graph_sector"}
    return [_with_neutral_signal_features(row, keep_sector=keep_sector, keep_graph=keep_graph) for row in enriched]


def _sector_signal_features(rows: list[dict[str, Any]], sector_by_symbol: dict[str, str], sector_index_by_sector: dict[str, str]) -> list[dict[str, Any]]:
    by_timestamp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_timestamp[str(row["timestamp"])].append(row)
    output: list[dict[str, Any]] = []
    for timestamp in sorted(by_timestamp):
        group = by_timestamp[timestamp]
        sector_returns: dict[str, list[float]] = defaultdict(list)
        for row in group:
            symbol = str(row["symbol"])
            sector = sector_by_symbol.get(symbol, "UNKNOWN")
            sector_returns[sector].append(float(row.get("return_1") or 0.0))
        sector_average = {sector: sum(values) / len(values) for sector, values in sector_returns.items()}
        for row in group:
            symbol = str(row["symbol"])
            sector = sector_by_symbol.get(symbol, "UNKNOWN")
            sector_return = sector_average.get(sector, 0.0)
            enriched = dict(row)
            enriched.update(
                {
                    "sector": sector,
                    "sector_index": sector_index_by_sector.get(sector, "NIFTY 50"),
                    "sector_return_source": "constituent_equal_weight_proxy",
                    "sector_return_1": sector_return,
                    "stock_minus_sector_return": float(row.get("return_1") or 0.0) - sector_return,
                }
            )
            output.append(enriched)
    return output


def _with_neutral_signal_features(row: dict[str, Any], *, keep_sector: bool, keep_graph: bool) -> dict[str, Any]:
    out = dict(row)
    if not keep_sector:
        out["sector"] = "UNKNOWN"
        out["sector_index"] = "NIFTY 50"
        out["sector_return_source"] = "disabled"
        out["sector_return_1"] = 0.0
        out["stock_minus_sector_return"] = 0.0
    else:
        out.setdefault("sector", "UNKNOWN")
        out.setdefault("sector_index", "NIFTY 50")
        out.setdefault("sector_return_source", "constituent_equal_weight_proxy")
        out.setdefault("sector_return_1", 0.0)
        out.setdefault("stock_minus_sector_return", 0.0)
    if not keep_graph:
        out["peer_return_1"] = 0.0
        out["peer_correlation"] = 0.0
        out["peer_edge_count"] = 0
        out["peer_confirmation"] = 0.0
    else:
        out.setdefault("peer_return_1", 0.0)
        out.setdefault("peer_correlation", 0.0)
        out.setdefault("peer_edge_count", 0)
        out.setdefault("peer_confirmation", 0.0)
    return out


def _load_minutes(lake_root: Path, symbols: set[str]) -> list[dict[str, Any]]:
    pattern = str(lake_root / "partitions" / "asset_type=equity" / "**" / "silver.parquet")
    if not list((lake_root / "partitions").glob("asset_type=equity/**/silver.parquet")):
        raise FileNotFoundError("no silver equity partitions found in lake")
    rows = (
        pl.scan_parquet(pattern, hive_partitioning=True)
        .filter(pl.col("symbol").str.to_uppercase().is_in(sorted(symbols)))
        .filter(pl.col("timestamp").str.slice(0, 10) >= pl.lit(GOLD_START))
        .filter(pl.col("timestamp").str.slice(0, 10) <= pl.lit(GOLD_END))
        .select(["symbol", "timestamp", "open", "high", "low", "close", "volume"])
        .collect(engine="streaming")
        .to_dicts()
    )
    global _MINUTE_CONTEXT, _MINUTE_TS_CONTEXT
    _MINUTE_CONTEXT, _MINUTE_TS_CONTEXT = _minute_context(rows)
    return rows


def _load_symbol_minutes(lake_root: Path, symbol: str) -> list[dict[str, Any]]:
    pattern = str(lake_root / "partitions" / "asset_type=equity" / "trading_date=*" / f"symbol={symbol}" / "silver.parquet")
    if not list((lake_root / "partitions").glob(f"asset_type=equity/trading_date=*/symbol={symbol}/silver.parquet")):
        return []
    rows = (
        pl.scan_parquet(pattern, hive_partitioning=True)
        .filter(pl.col("timestamp").str.slice(0, 10) >= pl.lit(GOLD_START))
        .filter(pl.col("timestamp").str.slice(0, 10) <= pl.lit(GOLD_END))
        .select(["symbol", "timestamp", "open", "high", "low", "close", "volume"])
        .collect(engine="streaming")
        .to_dicts()
    )
    global _MINUTE_CONTEXT, _MINUTE_TS_CONTEXT
    _MINUTE_CONTEXT, _MINUTE_TS_CONTEXT = _minute_context(rows)
    return rows


def _minute_context(rows: list[dict[str, Any]]) -> tuple[dict[tuple[str, str], list[dict[str, Any]]], dict[tuple[str, str], list[datetime]]]:
    answer: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    timestamps: dict[tuple[str, str], list[datetime]] = {}
    for row in rows:
        ts = _row_dt(row)
        answer[(str(row["symbol"]).upper(), ts.date().isoformat())].append(row)
    for key, values in answer.items():
        values.sort(key=_row_dt)
        timestamps[key] = [_row_dt(row) for row in values]
    return answer, timestamps


def _minute_map(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    return _minute_context(rows)[0]


def _lake_identity(root: Path) -> dict[str, object]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    return {"lake_format": manifest.get("format"), "snapshot_id": manifest.get("snapshot_id"), "manifest_sha256": _digest(manifest)}


def _row_dt(row: dict[str, Any]) -> datetime:
    cached = row.get("_timestamp_dt")
    if isinstance(cached, datetime):
        return cached
    parsed = _dt(row["timestamp"])
    row["_timestamp_dt"] = parsed
    return parsed


def _vol_normalized(value: float, volatility: float, clip: float = 8.0) -> float:
    """Standardize a return by its own rolling volatility, clipped for outliers."""
    if volatility <= 0:
        return 0.0
    return max(-clip, min(clip, value / volatility))


def _dt(value: object) -> datetime: return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
def _digest(value: object) -> str: return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
def _write_json(path: Path, value: object) -> None: path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
