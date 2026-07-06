from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .evidence import build_validation_only_evidence
from lake.gold import (
    GOLD_END,
    GOLD_FORMAT,
    GOLD_START,
    LABEL_VERSION,
    _digest,
    _feature_version,
    _lake_identity,
    _load_minutes,
    _normalize_fixed_label_contract,
    _universe_signal_metadata,
    _write_json as _write_gold_json,
    _write_labeled_gold_parts,
    causal_five_minute_features,
)
from evidence.validation import select_validation_only


LAKE = Path("data/phase1-run/lake-liquid50")
UNIVERSE = Path("data/phase1-run/universe/liquid50-pricefloor50.json")
TRAINING_END = "2022-12-31"
COST_BPS = 12.0
FEATURE_SET = "price_only"
MODEL_FAMILIES = ("random", "momentum", "vwap_relative_strength", "side_hist_gradient_boosting")
LONG_THRESHOLDS = (0.0, 0.1, 0.2)
SHORT_THRESHOLDS = (0.0, 0.1, 0.2)
TOP_K_LONG = 3
TOP_K_SHORT = 3
META_LABEL_MIN_PROBABILITY = 0.5
MIN_FOLLOWUP_TRADES = 100

BASELINE_CONTRACT = {"target_atr": 2.0, "stop_atr": 1.0, "timeout_minutes": 60}
SANITY_CONTRACTS = (
    {"target_atr": 1.5, "stop_atr": 1.0, "timeout_minutes": 45},
    {"target_atr": 2.5, "stop_atr": 1.5, "timeout_minutes": 60},
)


def run_price_contract_sanity(
    *,
    lake: Path = LAKE,
    universe: Path = UNIVERSE,
    run_root: Path | None = None,
) -> dict[str, Any]:
    """Run the two bounded price-only contract sanity tests on validation only."""
    resume = run_root is not None
    run_root = run_root or Path("data/phase1-run") / f"phase2-price-contract-sanity-{time.strftime('%Y%m%d-%H%M%S')}"
    run_root.mkdir(parents=True, exist_ok=resume)

    summary: dict[str, Any] = {
        "run_root": str(run_root),
        "lake": str(lake),
        "universe": str(universe),
        "sealed_2025_accessed": False,
        "policy": "validation_only_no_sealed_2025",
        "cost_bps": COST_BPS,
        "feature_set": FEATURE_SET,
        "model_families": list(MODEL_FAMILIES),
        "thresholds": {
            "long": list(LONG_THRESHOLDS),
            "short": list(SHORT_THRESHOLDS),
            "top_k_long": TOP_K_LONG,
            "top_k_short": TOP_K_SHORT,
            "meta_label_min_probability": META_LABEL_MIN_PROBABILITY,
        },
        "baseline_contract": BASELINE_CONTRACT,
        "sanity_contracts": list(SANITY_CONTRACTS),
        "contracts": {},
    }

    baseline_record = _run_one_contract(lake, universe, run_root, "baseline_2_0_1_0_60", BASELINE_CONTRACT, shared=None)
    summary["contracts"]["baseline_2_0_1_0_60"] = baseline_record
    baseline = baseline_record["best_by_rank"]

    missing_gold = [(contract, _contract_key(contract)) for contract in SANITY_CONTRACTS if not _paths(run_root, _contract_key(contract))["gold"].exists()]
    if missing_gold:
        shared = _shared_decision_surface(lake, universe)
        for contract, key in missing_gold:
            paths = _paths(run_root, key)
            print(f"[{key}] write fixed-contract gold", flush=True)
            _write_gold_from_decisions(shared, paths["gold"], contract)
        del shared

    followup_candidates = 0
    for contract in SANITY_CONTRACTS:
        key = _contract_key(contract)
        record = _run_one_contract(lake, universe, run_root, key, contract, shared=None)
        record["research_gate"] = research_gate(record["best_by_rank"], baseline)
        followup_candidates += int(record["research_gate"]["passed"])
        summary["contracts"][key] = record
        _write_json(run_root / "summary.partial.json", summary)

    summary["exit_condition"] = (
        "one_or_more_contracts_passed_research_gate_confirm_before_any_engineering"
        if followup_candidates
        else "both_contracts_failed_research_gate_stop_price_only_contract_work"
    )
    _write_json(run_root / "summary.json", summary)
    return summary


def research_gate(candidate: dict[str, Any] | None, baseline: dict[str, Any] | None) -> dict[str, Any]:
    candidate = candidate or {}
    baseline = baseline or {}
    checks = {
        "ev_improved": float(candidate.get("expected_value_bps", 0.0)) > float(baseline.get("expected_value_bps", 0.0)),
        "pf_improved": float(candidate.get("profit_factor", 0.0)) > float(baseline.get("profit_factor", 0.0)),
        "ci_low_improved": float(candidate.get("ci_low_bps", 0.0)) > float(baseline.get("ci_low_bps", 0.0)),
        "min_followup_trades": int(candidate.get("trades", 0)) >= MIN_FOLLOWUP_TRADES,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "candidate": _gate_metrics(candidate),
        "baseline": _gate_metrics(baseline),
        "concentration": {
            "max_symbol_trade_share": float(candidate.get("max_symbol_trade_share", 0.0)),
            "distinct_symbols": int(candidate.get("distinct_symbols", 0)),
        },
    }


def _run_one_contract(lake: Path, universe: Path, run_root: Path, name: str, contract: dict[str, Any], *, shared: dict[str, Any] | None) -> dict[str, Any]:
    paths = _paths(run_root, name)
    if paths["gold"].exists():
        gold = json.loads(paths["gold"].with_suffix(".manifest.json").read_text(encoding="utf-8"))
        print(f"[{name}] reuse gold", flush=True)
    else:
        print(f"[{name}] write fixed-contract gold", flush=True)
        if shared is None:
            shared = _shared_decision_surface(lake, universe)
        gold = _write_gold_from_decisions(shared, paths["gold"], contract)

    if paths["selection"].exists():
        selection = json.loads(paths["selection"].read_text(encoding="utf-8"))
        print(f"[{name}] reuse validation selection", flush=True)
    else:
        print(f"[{name}] select validation", flush=True)
        selection = select_validation_only(
            paths["gold"],
            paths["selection"],
            thresholds=[0.0],
            long_thresholds=LONG_THRESHOLDS,
            short_thresholds=SHORT_THRESHOLDS,
            top_k_long=TOP_K_LONG,
            top_k_short=TOP_K_SHORT,
            meta_label_min_probability=META_LABEL_MIN_PROBABILITY,
            model_families=MODEL_FAMILIES,
        )
    if paths["validation_report"].exists():
        print(f"[{name}] reuse validation evidence", flush=True)
    else:
        print(f"[{name}] validation evidence", flush=True)
        build_validation_only_evidence(paths["selection"], paths["validation_report"])
    report = json.loads(paths["validation_report"].read_text(encoding="utf-8"))
    best = report.get("best_by_rank") or {}
    return {
        "contract": dict(contract),
        "tradability": report.get("tradability"),
        "selection_reason": report.get("selection_reason"),
        "gold": gold,
        "selected": selection.get("selected"),
        "best_by_rank": best,
        "sealed_test": {"skipped": True, "reason": "validation_only_price_contract_sanity"},
        "paths": {key: str(value) for key, value in paths.items()},
    }


def _shared_decision_surface(lake: Path, universe_manifest: Path) -> dict[str, Any]:
    print("[shared] load universe and lake minutes", flush=True)
    universe = json.loads(universe_manifest.read_text(encoding="utf-8"))
    symbols = sorted({str(item["symbol"]).upper() for item in universe["symbols"]})
    sectors, sector_indices = _universe_signal_metadata(universe)
    source_identity = _lake_identity(lake)
    print("[shared] build price-only causal decisions", flush=True)
    minutes = _load_minutes(lake, set(symbols))
    decisions = causal_five_minute_features(minutes, set(symbols), sectors, sector_indices, feature_set=FEATURE_SET)
    return {
        "universe": universe,
        "universe_manifest": universe_manifest,
        "source_identity": source_identity,
        "decisions": decisions,
        "feature_version": _feature_version(FEATURE_SET),
    }


def _write_gold_from_decisions(shared: dict[str, Any], output: Path, contract: dict[str, Any]) -> dict[str, Any]:
    selected = _normalize_fixed_label_contract(contract)
    rows_written, ambiguity_count = _write_labeled_gold_parts(shared["decisions"], output, selected, COST_BPS)
    label_identity = {"version": LABEL_VERSION, "contract": selected, "selection_cutoff": TRAINING_END}
    cost_identity = {"version": "generic-all-in-friction-v1", "applied_once_per_completed_trade": True, "base_cost_bps": COST_BPS, "sensitivity_bps": [8, 12, 16, 20]}
    universe = shared["universe"]
    identity = {
        "format": GOLD_FORMAT,
        "source": shared["source_identity"],
        "universe_manifest_id": universe["manifest_id"],
        "study_rows": {"start": GOLD_START, "end": GOLD_END, "future_rows_reserved_from": "2026-01-01"},
        "feature": shared["feature_version"],
        "feature_set": FEATURE_SET,
        "feature_sets_available": ["graph", "graph_sector", "price_only", "sector"],
        "label": label_identity,
        "cost": cost_identity,
    }
    manifest = {
        **identity,
        "gold_id": _digest(identity),
        "rows": rows_written,
        "ambiguous_rows": ambiguity_count,
        "output": str(output),
        "universe_manifest": str(shared["universe_manifest"]),
    }
    _write_gold_json(output.with_suffix(".manifest.json"), manifest)
    _write_gold_json(
        output.with_suffix(".label-contract.json"),
        {
            "format": "meridian.label-contract.v1",
            "contract_id": _digest(label_identity),
            **label_identity,
            "candidates": [dict(target_atr=selected["target_atr"], stop_atr=selected["stop_atr"], timeout_minutes=selected["timeout_minutes"])],
        },
    )
    return {"rows_written": rows_written, "ambiguous_rows": ambiguity_count, "output": str(output), "manifest": str(output.with_suffix(".manifest.json")), "label_contract": str(output.with_suffix(".label-contract.json")), "gold_id": manifest["gold_id"], "feature_set": FEATURE_SET, "feature": shared["feature_version"]}


def _paths(run_root: Path, name: str) -> dict[str, Path]:
    root = run_root / name
    return {
        "root": root,
        "gold": root / "gold" / "causal.parquet",
        "selection": root / "validation" / "selection.json",
        "validation_report": root / "validation" / "report.json",
    }


def _contract_key(contract: dict[str, Any]) -> str:
    target = str(contract["target_atr"]).replace(".", "_")
    stop = str(contract["stop_atr"]).replace(".", "_")
    timeout = str(contract["timeout_minutes"])
    return f"contract_{target}_{stop}_{timeout}"


def _gate_metrics(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "trades": int(item.get("trades", 0)),
        "expected_value_bps": float(item.get("expected_value_bps", 0.0)),
        "profit_factor": float(item.get("profit_factor", 0.0)),
        "ci_low_bps": float(item.get("ci_low_bps", 0.0)),
    }


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
