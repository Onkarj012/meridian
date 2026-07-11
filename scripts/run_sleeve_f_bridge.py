#!/usr/bin/env python3
"""Run the declared Sleeve F bridge gate and write its JSON report."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evidence.sleeve_f_router_replay import load_router_model, replay, summarize
from features.sleeve_f_router import build_proxy_features

TRADE_COUNT_TOLERANCE = 0.01
NET_PNL_TOLERANCE = 0.01
EXIT_COUNT_TOLERANCE = 0.02
WINDOW_START = pd.Timestamp("2024-11-01")
WINDOW_END = pd.Timestamp("2026-05-31")


def compare(actual: dict, reference: dict) -> dict:
    def delta(value: float, baseline: float) -> dict:
        if baseline:
            relative_delta = (value - baseline) / baseline
        else:
            relative_delta = 0.0 if value == baseline else float("inf")
        return {"actual": value, "reference": baseline, "delta": value - baseline, "relative_delta": relative_delta}

    metrics = {
        "trade_count": {**delta(actual["trade_count"], reference["trade_count"]), "tolerance": TRADE_COUNT_TOLERANCE},
        "net_pnl_inr": {**delta(actual["net_pnl_inr"], reference["net_pnl_inr"]), "tolerance": NET_PNL_TOLERANCE},
        "exit_counts": {},
    }
    passes = [abs(metrics["trade_count"]["relative_delta"]) <= TRADE_COUNT_TOLERANCE, abs(metrics["net_pnl_inr"]["relative_delta"]) <= NET_PNL_TOLERANCE]
    for reason in ("TIME", "STOP", "TARGET"):
        item = delta(actual["exit_counts"].get(reason, 0), reference["exit_counts"].get(reason, 0))
        item["tolerance"] = EXIT_COUNT_TOLERANCE
        metrics["exit_counts"][reason] = item
        passes.append(abs(item["relative_delta"]) <= EXIT_COUNT_TOLERANCE)
    return {"passed": all(passes), "metrics": metrics}


def delta_summary(legacy: dict, causal: dict) -> dict:
    return {
        "trade_count": causal["trade_count"] - legacy["trade_count"],
        "net_pnl_inr": causal["net_pnl_inr"] - legacy["net_pnl_inr"],
        "sharpe_daily_ann": causal["sharpe_daily_ann"] - legacy["sharpe_daily_ann"],
        "exit_counts": {
            reason: causal["exit_counts"].get(reason, 0) - legacy["exit_counts"].get(reason, 0)
            for reason in ("TIME", "STOP", "TARGET")
        },
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_features(args: argparse.Namespace) -> tuple[pd.DataFrame, dict, str]:
    if args.features_cache:
        cache = args.features_cache.resolve()
        features = pd.read_parquet(cache)
        features["datetime"] = pd.to_datetime(features["datetime"])
        features["trade_date"] = pd.to_datetime(features["trade_date"])
        return features, {"kind": "frozen_proxy_feature_cache", "path": str(cache), "sha256": _sha256(cache)}, "_frozen_cache"

    raw = pd.read_csv(args.source_root / "data/nifty_intraday/NIFTY 50_minute.csv")
    raw["_datetime"] = pd.to_datetime(raw["date"])
    raw = raw[(raw["_datetime"] >= WINDOW_START) & (raw["_datetime"] <= WINDOW_END)].drop(columns="_datetime")
    return build_proxy_features(raw), {"kind": "rebuilt_from_nifty_minute_csv", "path": str((args.source_root / "data/nifty_intraday/NIFTY 50_minute.csv").resolve()), "sha256": None}, ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=PROJECT_ROOT.parent / "intranet_optinet")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "runs/sleeve-f-bridge")
    parser.add_argument("--features-cache", type=Path, help="Use an existing, frozen proxy feature cache instead of rebuilding features.")
    args = parser.parse_args()

    features, input_metadata, suffix = _load_features(args)
    model = load_router_model()
    legacy_trades, legacy_metadata = replay(features, model, variant="legacy_parity")
    reference_trades = pd.read_parquet(args.source_root / "results/router_v0/phase3_fwd_no_guard.parquet")
    bridge = compare(summarize(legacy_trades), summarize(reference_trades))
    report = {"window": [str(WINDOW_START.date()), str(WINDOW_END.date())], "metadata": {**legacy_metadata, "input": input_metadata}, "actual": summarize(legacy_trades), "reference": summarize(reference_trades), "bridge": bridge}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / f"bridge_report{suffix}.json").write_text(json.dumps(report, indent=2, sort_keys=True))
    if not bridge["passed"]:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1

    causal_trades, causal_metadata = replay(features, model, variant="causal")
    causal = summarize(causal_trades)
    causal_report = {"window": [str(WINDOW_START.date()), str(WINDOW_END.date())], "metadata": {**causal_metadata, "input": input_metadata}, "causal": causal, "legacy_parity": report["actual"], "legacy_vs_causal_delta": delta_summary(report["actual"], causal)}
    (args.output_dir / f"causal_report{suffix}.json").write_text(json.dumps(causal_report, indent=2, sort_keys=True))
    print(json.dumps(report, indent=2, sort_keys=True))
    print(json.dumps(causal_report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
