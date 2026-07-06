"""Immutable, safety-first evidence report for a frozen liquid-20 study."""
from __future__ import annotations

import hashlib
import json
import math
import resource
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import duckdb

from evidence.stats import economic_metrics
from evidence.validation import _assign_actions, _calibrate, _fit_calibrator, _load_gold, _oof_scores, _raw_scores, temporal_split


REPORT_FORMAT = "meridian.liquid-20-evidence.v1"
VALIDATION_REPORT_FORMAT = "meridian.validation-only-evidence.v1"
STATUSES = {"tradable_candidate", "not_tradable"}
PHASE1B_BASELINE = {"model_family": "side_hist_gradient_boosting", "hit_rate": 0.417, "expected_value_bps": -4.09, "random_hit_rate": 0.396, "breakeven_hit_rate_8bps": 0.476}


def build_validation_only_evidence(sealed_selection: Path, report_path: Path) -> dict[str, Any]:
    """Summarize validation candidates without requiring or spending sealed 2025."""
    sealed_selection, report_path = Path(sealed_selection), Path(report_path)
    selection = _json_required(sealed_selection)
    identity = {"selection_sha256": _sha(sealed_selection), "experiment_id": selection.get("experiment_id"), "baseline": PHASE1B_BASELINE}
    if report_path.exists():
        existing = _json_required(report_path)
        if existing.get("format") == VALIDATION_REPORT_FORMAT and existing.get("identity") == identity:
            return {"report": str(report_path), "experiment_id": existing["experiment_id"], "tradability": existing["tradability"], "reused": True}
        raise ValueError(f"immutable validation report already exists with different dependencies: {report_path}")
    candidates = [_validation_candidate_summary(candidate) for candidate in selection.get("candidates", [])]
    report = {
        "format": VALIDATION_REPORT_FORMAT,
        "experiment_id": selection.get("experiment_id"),
        "tradability": selection.get("tradability", "not_tradable"),
        "selection_reason": selection.get("selection_reason"),
        "sealed_2025_accessed": bool(selection.get("test_accessed")),
        "identity": identity,
        "baseline": PHASE1B_BASELINE,
        "selection_inputs": selection.get("selection_inputs", {}),
        "selected": selection.get("selected"),
        "candidates": candidates,
        "best_by_rank": max(candidates, key=lambda item: (item["robust_validation_eligible"], item["ci_low_bps"], item["profit_factor"], item["trades"]), default=None),
    }
    _write_immutable(report_path, report)
    return {"report": str(report_path), "experiment_id": report["experiment_id"], "tradability": report["tradability"]}


def _validation_candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    metrics = candidate.get("validation", {})
    trades = int(metrics.get("long", 0)) + int(metrics.get("short", 0))
    return {
        "model_family": candidate.get("model_family"),
        "confidence_threshold": candidate.get("confidence_threshold"),
        "long_threshold": candidate.get("long_threshold", candidate.get("confidence_threshold")),
        "short_threshold": candidate.get("short_threshold", candidate.get("confidence_threshold")),
        "meta_label_min_probability": candidate.get("meta_label_min_probability"),
        "robust_validation_eligible": bool(candidate.get("robust_validation_eligible")),
        "rejection_reasons": candidate.get("rejection_reasons", []),
        "execution_caveats": metrics.get("execution_caveats", []),
        "trades": trades,
        "trading_days": int(metrics.get("trading_days", 0)),
        "long": int(metrics.get("long", 0)),
        "short": int(metrics.get("short", 0)),
        "hit_rate": float(metrics.get("trade_conditioned_hit_rate", 0.0)),
        "hit_rate_delta_vs_phase1b": float(metrics.get("trade_conditioned_hit_rate", 0.0)) - PHASE1B_BASELINE["hit_rate"] if trades else 0.0,
        "expected_value_bps": float(metrics.get("expected_value_bps", 0.0)),
        "ev_delta_vs_phase1b_bps": float(metrics.get("expected_value_bps", 0.0)) - PHASE1B_BASELINE["expected_value_bps"] if trades else 0.0,
        "profit_factor": float(metrics.get("net_profit_factor", 0.0)),
        "ci_low_bps": float(metrics.get("net_expectancy_ci95_low_bps", 0.0)),
        "max_drawdown_bps": float(metrics.get("net_max_drawdown_bps", 0.0)),
        "max_symbol_trade_share": float(metrics.get("max_symbol_trade_share", 0.0)),
        "distinct_symbols": int(metrics.get("distinct_symbols", 0)),
        "max_side_trade_share": float(metrics.get("max_side_trade_share", 0.0)),
        "side_results": metrics.get("side_results", {}),
        "cost_scenarios": metrics.get("cost_scenarios", {}),
        "cost_sensitivity": metrics.get("cost_sensitivity", {}),
        "execution_scenarios": metrics.get("execution_scenarios", {}),
        "meta_filter": metrics.get("meta_filter", {}),
        "pre_filter": metrics.get("pre_filter"),
    }


def build_liquid20_evidence(
    lake_root: Path, universe_manifest: Path, gold_path: Path, sealed_selection: Path,
    sealed_result: Path, report_path: Path, *, source_snapshot: Path | None = None,
) -> dict[str, Any]:
    """Produce a reproducible report without loading lake source rows into Python.

    The lake is interrogated through DuckDB aggregate queries only.  Gold is a
    small, purpose-built derived decision artifact and is the only row-level
    input used to calculate model outcome slices.
    """
    started, peak_before = time.perf_counter(), _peak_memory_bytes()
    lake_root, universe_manifest = Path(lake_root), Path(universe_manifest)
    gold_path, sealed_selection, sealed_result, report_path = map(Path, (gold_path, sealed_selection, sealed_result, report_path))
    lake = _json_required(lake_root / "manifest.json")
    universe, selection, result = map(_json_required, (universe_manifest, sealed_selection, sealed_result))
    gold_manifest = _json_optional(gold_path.with_suffix(".manifest.json"))
    label_contract = _json_optional(gold_path.with_suffix(".label-contract.json"))
    snapshot = _json_optional(source_snapshot) if source_snapshot else None
    identities = _identities(lake, universe, gold_manifest, label_contract, selection, result, snapshot, gold_path, sealed_selection, sealed_result)
    dependency_identity = _digest(identities)
    if report_path.exists():
        existing = _json_required(report_path)
        if existing.get("format") == REPORT_FORMAT and existing.get("dependency_identity") == dependency_identity:
            return {"report": str(report_path), "experiment_id": existing["experiment_id"], "tradability": existing["tradability"], "reused": True}
        raise ValueError(f"immutable evidence report already exists with different dependencies: {report_path}")
    lake_query = _lake_query(lake_root, {str(x["symbol"]).upper() for x in universe.get("symbols", [])}, lake)
    quality = _quality(lake)
    rows, gold_identity = _load_gold(gold_path)
    scored = _scored_test_rows(rows, selection)
    outcomes = _outcomes(scored)
    breakdowns = _breakdowns(scored, universe)
    gates = _gate(identities, lake_query, quality, result, outcomes)
    status = "tradable_candidate" if all(gates.values()) else "not_tradable"
    assert status in STATUSES
    experiment_id = _digest({"format": REPORT_FORMAT, "dependencies": dependency_identity})
    telemetry = {
        "source_access": "duckdb_partition_aggregate_only",
        "source_rows_python_materialized": False,
        "wall_time_seconds": round(time.perf_counter() - started, 6),
        "peak_memory_bytes": max(peak_before, _peak_memory_bytes()),
        "lake_rows_read": lake_query["rows"], "partitions_read": lake_query["partitions"],
    }
    report = {
        "format": REPORT_FORMAT, "experiment_id": experiment_id, "dependency_identity": dependency_identity,
        "tradability": status, "acceptance_gates": {"checks": gates, "passed": all(gates.values())},
        "identities": identities, "lake": lake_query, "quality": quality, "telemetry": telemetry,
        "sealed_test": {"experiment_id": result.get("experiment_id"), "metrics": result.get("test"), "selected": result.get("selected"), "feature_coverage": selection.get("selection_inputs", {}).get("feature_coverage", {})},
        "outcomes": outcomes, "breakdowns": breakdowns, "gold_identity": gold_identity,
    }
    _write_immutable(report_path, report)
    return {"report": str(report_path), "experiment_id": experiment_id, "tradability": status}


def _lake_query(root: Path, symbols: set[str], lake_manifest: dict[str, Any]) -> dict[str, Any]:
    files = list((root / "partitions").glob("asset_type=equity/**/silver.parquet"))
    if not files:
        return {"rows": 0, "partitions": 0, "reused_partitions": int(lake_manifest.get("reused_partitions", 0)), "symbols": 0, "complete_partitions": 0, "expected_partitions": int(lake_manifest.get("partitions", 0)), "query": "no_equity_partitions"}
    # Values are passed as parameters; source parquet never becomes Python rows.
    con = duckdb.connect(":memory:")
    try:
        glob = str(root / "partitions" / "asset_type=equity" / "**" / "silver.parquet")
        if symbols:
            placeholders = ",".join("?" for _ in symbols)
            row = con.execute(f"select count(*), count(distinct symbol) from read_parquet(?, hive_partitioning=1) where upper(symbol) in ({placeholders})", [glob, *sorted(symbols)]).fetchone()
        else: row = (0, 0)
    finally: con.close()
    manifests = list((root / "partitions").glob("asset_type=equity/**/manifest.json"))
    completed = sum(bool(_json_required(path).get("completed")) for path in manifests)
    expected = sum(1 for record in lake_manifest.get("partition_manifests", []) if "asset_type=equity" in str(record.get("path", ""))) or len(manifests)
    return {"rows": int(row[0]), "symbols": int(row[1]), "partitions": len(files), "reused_partitions": int(lake_manifest.get("reused_partitions", 0)), "complete_partitions": completed, "expected_partitions": expected, "query": "duckdb_read_parquet_partitioned"}


def _scored_test_rows(rows: list[dict[str, Any]], selection: dict[str, Any]) -> list[dict[str, Any]]:
    parts = temporal_split(rows)
    if not parts["test"] or not parts["train"]: return []
    choice, inputs = selection.get("selected", {}), selection.get("selection_inputs", {})
    family, horizon = str(choice.get("model_family", "")), int(inputs.get("horizon_minutes", 30))
    oof = _oof_scores(family, parts["train"], horizon)
    probabilities = _calibrate(_fit_calibrator(oof[0], oof[1]), _raw_scores(family, parts["train"] + parts["validation"], parts["test"]))
    threshold = float(choice.get("confidence_threshold", 1.1))
    assigned = _assign_actions(parts["test"], probabilities, threshold, inputs.get("decision_top_k"))
    answer = []
    for index, (row, (long_p, short_p)) in enumerate(zip(parts["test"], probabilities)):
        side = assigned[index]
        gross = float(row.get("long_gross_return_bps", 0) if side == "LONG" else row.get("short_gross_return_bps", 0)) if side != "NO_TRADE" else 0.0
        cost = float(row.get("cost_bps", 0) or 0)
        actual = "LONG" if row.get("long_label") == "LONG_SUCCESS" else "SHORT" if row.get("short_label") == "SHORT_SUCCESS" else "NO_TRADE"
        answer.append({**row, "side": side, "actual": actual, "confidence": max(long_p, short_p), "gross_bps": gross, "net_bps": gross - cost if side != "NO_TRADE" else 0.0})
    return answer


def _outcomes(rows: list[dict[str, Any]]) -> dict[str, Any]:
    trades = [row for row in rows if row["side"] != "NO_TRADE"]
    gross, net = [float(x["gross_bps"]) for x in trades], [float(x["net_bps"]) for x in trades]
    daily: dict[str, float] = defaultdict(float)
    for row in trades: daily[str(row["timestamp"])[:10]] += float(row["net_bps"])
    metrics = {"gross": economic_metrics(gross, []), "generic_net": economic_metrics(net, list(daily.values()))}
    metrics["generic_net"]["calmar"] = (sum(daily.values()) / len(daily) * 252 / abs(metrics["generic_net"]["max_drawdown_bps"])) if daily and metrics["generic_net"]["max_drawdown_bps"] < 0 else 0.0
    metrics["turnover"] = len(trades) / len(rows) if rows else 0.0
    metrics["trading_days"] = len(daily)
    totals = Counter(str(x["symbol"]) for x in trades)
    metrics["concentration"] = {"max_symbol_trade_share": max(totals.values()) / len(trades) if trades else 0.0, "distinct_symbols": len(totals), "symbol_trade_counts": dict(sorted(totals.items()))}
    labels = ("LONG", "SHORT", "NO_TRADE")
    metrics["confusion_matrix"] = {a: {b: sum(x["side"] == a and x["actual"] == b for x in rows) for b in labels} for a in labels}
    metrics["return_distribution"] = _distribution(net)
    metrics["win_loss_streaks"] = _streaks(net)
    metrics["trades"] = len(trades); metrics["rows"] = len(rows)
    return metrics


def _breakdowns(rows: list[dict[str, Any]], universe: dict[str, Any]) -> dict[str, Any]:
    sectors = {str(x["symbol"]).upper(): str(x.get("sector", "unknown")) for x in universe.get("symbols", [])}
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {key: defaultdict(list) for key in ("side", "symbol", "sector", "month", "rolling_20_sessions", "trend_regime", "volatility_regime", "gap_regime", "session_phase")}
    by_dates = sorted({str(x["timestamp"])[:10] for x in rows}); date_block = {day: f"{i // 20 * 20 + 1}-{min(i // 20 * 20 + 20, len(by_dates))}" for i, day in enumerate(by_dates)}
    for row in rows:
        ts = str(row["timestamp"]); hour_minute = ts[11:16]
        trend = "up" if float(row.get("return_3", row.get("return_1", 0)) or 0) > 0 else "down_or_flat"
        vol = "high" if abs(float(row.get("return_1", 0) or 0)) >= 0.01 else "low"
        gap = "gap" if abs(float(row.get("return_1", 0) or 0)) >= 0.005 else "no_gap"
        phase = "open" if hour_minute < "10:00" else "mid" if hour_minute < "14:30" else "close"
        keys = {"side": row["side"], "symbol": str(row["symbol"]), "sector": sectors.get(str(row["symbol"]).upper(), "unknown"), "month": ts[:7], "rolling_20_sessions": date_block[ts[:10]], "trend_regime": trend, "volatility_regime": vol, "gap_regime": gap, "session_phase": phase}
        for name, value in keys.items(): grouped[name][value].append(row)
    return {name: {key: _summary(value) for key, value in sorted(groups.items())} for name, groups in grouped.items()}


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    trade = [float(x["net_bps"]) for x in rows if x["side"] != "NO_TRADE"]
    return {"rows": len(rows), "trades": len(trade), "generic_net": economic_metrics(trade)}


def _quality(lake: dict[str, Any]) -> dict[str, Any]:
    manifests = []
    seen_sources: set[tuple[str, str]] = set()
    for record in lake.get("partition_manifests", []):
        path = Path(str(record.get("path", "")))
        asset = next((part.removeprefix("asset_type=") for part in path.parts if part.startswith("asset_type=")), "")
        symbol = next((part.removeprefix("symbol=") for part in path.parts if part.startswith("symbol=")), "")
        key = (asset, symbol)
        # Source diagnostics are copied into every date partition. Aggregate one
        # manifest per source so counts and samples are not multiplied by days.
        if key in seen_sources:
            continue
        seen_sources.add(key)
        if path.exists(): manifests.append(_json_required(path))
    counts: Counter[str] = Counter(); samples = []
    for item in manifests:
        quality = item.get("quality", {}); counts.update(quality.get("counts", {})); samples.extend(quality.get("rejected_samples", []))
    quarantined = int(lake.get("quarantined_rows", 0))
    policy = lake.get("quarantine_policy", {})
    resolved = bool(policy.get("resolved_by")) and bool(policy.get("material_rejection_reasons"))
    unresolved = 0 if resolved else quarantined
    return {"quarantined_rows": quarantined, "failure_counts": dict(sorted(counts.items())), "rejected_row_samples": samples[:100], "quarantine_policy": policy, "resolved_quarantined_rows": quarantined if resolved else 0, "material_breaches": unresolved}


def _identities(lake: dict[str, Any], universe: dict[str, Any], gold: dict[str, Any] | None, label: dict[str, Any] | None, selection: dict[str, Any], result: dict[str, Any], snapshot: dict[str, Any] | None, *paths: Path) -> dict[str, Any]:
    return {"source_snapshot": snapshot.get("snapshot_id") if snapshot else lake.get("snapshot_id"), "universe": universe.get("manifest_id"), "metadata": universe.get("metadata_sha256"), "feature": gold.get("feature") if gold else None, "label": label.get("contract_id") if label else None, "cost": gold.get("cost") if gold else None, "calibration": selection.get("selection_inputs", {}).get("calibration"), "model": selection.get("selected", {}).get("model_family"), "sealed_configuration": selection.get("experiment_id"), "sealed_result": result.get("experiment_id"), "partition_manifest": _digest(lake.get("partition_manifests", [])), "artifacts": {str(path): _sha(path) if path.exists() else None for path in paths}}


def _gate(ids: dict[str, Any], lake: dict[str, Any], quality: dict[str, Any], result: dict[str, Any], outcomes: dict[str, Any]) -> dict[str, bool]:
    net = outcomes["generic_net"]
    required = ("source_snapshot", "universe", "metadata", "feature", "label", "cost", "calibration", "model", "sealed_configuration", "sealed_result", "partition_manifest")
    return {"provenance_complete": all(ids.get(k) is not None for k in required), "partitions_complete": lake["rows"] > 0 and lake["partitions"] > 0 and lake["complete_partitions"] == lake["expected_partitions"], "quality_resolved": quality["material_breaches"] == 0, "sealed_result_matches_configuration": result.get("experiment_id") == ids.get("sealed_configuration"), "has_sealed_trades": outcomes["trades"] > 0, "sealed_trade_count": outcomes["trades"] >= 250, "sealed_trading_days": outcomes.get("trading_days", 0) >= 60, "positive_bootstrap_ci": net["expectancy_ci95_low_bps"] > 0, "profit_factor_above_one": net["profit_factor"] > 1.0}


def _distribution(values: list[float]) -> dict[str, float]:
    if not values: return {"min": 0.0, "p05": 0.0, "median": 0.0, "p95": 0.0, "max": 0.0}
    ordered = sorted(values)
    at = lambda q: ordered[min(len(ordered)-1, max(0, int(q*(len(ordered)-1))))]
    return {"min": ordered[0], "p05": at(.05), "median": at(.5), "p95": at(.95), "max": ordered[-1]}
def _streaks(values: list[float]) -> dict[str, int]:
    best = {"win": 0, "loss": 0}; current = {"win": 0, "loss": 0}
    for value in values:
        kind = "win" if value > 0 else "loss" if value < 0 else None
        for key in current: current[key] = current[key] + 1 if key == kind else 0; best[key] = max(best[key], current[key])
    return {"longest_win": best["win"], "longest_loss": best["loss"]}
def _json_required(path: Path) -> dict[str, Any]: return json.loads(path.read_text(encoding="utf-8"))
def _json_optional(path: Path | None) -> dict[str, Any] | None: return _json_required(path) if path and path.exists() else None
def _sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def _digest(value: Any) -> str: return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
def _peak_memory_bytes() -> int: return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * (1 if __import__("sys").platform == "darwin" else 1024)
def _write_immutable(path: Path, value: dict[str, Any]) -> None:
    encoded = json.dumps(value, indent=2, sort_keys=True) + "\n"; path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") != encoded: raise ValueError(f"immutable evidence report already exists with different content: {path}")
    if not path.exists(): path.write_text(encoded, encoding="utf-8")
