#!/usr/bin/env python3
"""Deterministic, protocol-guarded runner for intraday-pred v1."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from contracts.intraday_targets_v1 import make_targets
from evidence.intraday_folds_v1 import (
    PROTOCOL_PATH, apply_fold, apply_final_split, assert_cutoff_safe,
    assert_development_safe, final_split, intraday_folds, load_evaluation_protocol,
)
from evidence.intraday_metrics_v1 import (
    direction_baselines, direction_metrics, development_gate_verdicts,
    fixed_magnitude_baselines, magnitude_metrics, multiclass_brier,
    non_overlapping_decisions,
)
from features.intraday_external import load_intraday_external_features
from features.intraday_prediction_v1 import FEATURE_MANIFESTS, build_prediction_features_v1
from models.intraday_confidence import calibrate_confidence, correctness_labels, fit_correctness_calibrator
from models.intraday_predictor import SEED
from models.intraday_predictor_v1 import fit_horizon_models, predict_horizon


DATA_PATH = REPO_ROOT / "runs/sleeve-f-c1-matrix/c1.parquet"
DEFAULT_OUT_DIR = Path("runs/intraday-pred-v1")
READ_COLUMNS = [
    "datetime", "trade_date", "f_open", "f_high", "f_low", "f_close", "f_vol", "f_oi",
    "log_ret_1m", "log_ret_5m", "log_ret_15m", "log_ret_30m", "realized_vol_5m", "realized_vol_15m", "realized_vol_30m",
    "atr_5m", "atr_15m", "atr_30m", "vwap_dev", "vwap_slope_5m", "or_dist_high", "or_dist_low", "or_breakout_up", "or_breakout_dn",
    "oi_chg_1m", "oi_chg_5m", "oi_chg_30m", "oi_long_buildup", "oi_short_buildup", "oi_short_cover", "oi_long_unwind",
    "vol_zscore", "vol_oi_ratio", "basis", "basis_chg_30m", "ema_slope", "consec_bars", "regime",
]
HORIZONS = (15, 60)
CANDIDATES = ("V1-A", "V1-B", "V1-C")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--data-path", type=Path, default=DATA_PATH)
    parser.add_argument("--bootstrap", type=int, default=None)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--dev", action="store_true", help="run registered development folds")
    parser.add_argument("--final-test", "--final", dest="final_test", action="store_true")
    parser.add_argument("--unseal", action="store_true")
    parser.add_argument("--registration-hash", default=None)
    return parser


def load_cutoff_safe(path: Path = DATA_PATH) -> tuple[pd.DataFrame, str]:
    """Predicate-scan the sealed boundary before converting the C1 table."""
    protocol = load_evaluation_protocol()
    cutoff = protocol["sealed_test"].split("/")[0]
    schema = pq.read_schema(path)
    columns = [column for column in READ_COLUMNS if column in schema.names]
    missing = sorted(set(READ_COLUMNS).difference(columns))
    if missing:
        raise ValueError(f"intraday input is missing columns: {', '.join(missing)}")
    table = pq.read_table(path, columns=columns, filters=[[('datetime', '<', pd.Timestamp(cutoff))]])
    frame = table.to_pandas()
    if frame.empty:
        raise ValueError("cutoff-safe parquet scan returned no rows")
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="raise")
    assert_cutoff_safe(frame, cutoff=cutoff)
    return frame.sort_values("datetime", kind="stable").reset_index(drop=True), _frame_hash(frame)


def run_experiment(
    frame: pd.DataFrame,
    out_dir: Path,
    *,
    bootstrap: int | None = None,
    smoke: bool = False,
    final_test: bool = False,
    unseal: bool = False,
    registration_hash: str | None = None,
    data_hash: str | None = None,
    external_features: pd.DataFrame | None = None,
    external_loader_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run supplied data, making test seams independent of the parquet source."""
    protocol = load_evaluation_protocol()
    draws = int(bootstrap if bootstrap is not None else protocol["bootstrap"]["draws"])
    if draws < 1:
        raise ValueError("bootstrap must be positive")
    if final_test:
        _assert_final_authorized(out_dir, unseal=unseal, registration_hash=registration_hash)
    prepared = frame.copy()
    prepared["datetime"] = pd.to_datetime(prepared["datetime"], errors="raise")
    prepared = prepared.sort_values("datetime", kind="stable").reset_index(drop=True)
    assert_cutoff_safe(prepared)
    if not final_test:
        assert_development_safe(prepared)
    if smoke:
        prepared = _last_sessions(prepared, 6)
    targets = make_targets(prepared, HORIZONS).reset_index(drop=True)
    external_audit: dict[str, Any] = {}
    if external_features is None and not smoke and any(candidate != "V1-A" for candidate in CANDIDATES):
        external_features, external_audit = load_intraday_external_features(
            prepared.drop(columns=["trade_date"], errors="ignore"), pd.Timestamp(protocol["sealed_test"].split("/")[0] if final_test else protocol["burned_periods"][0].split("/")[0]), "V1-C", **(external_loader_kwargs or {}),
        )
    elif external_features is None and smoke:
        external_features, external_audit = load_intraday_external_features(
            prepared.drop(columns=["trade_date"], errors="ignore"), pd.Timestamp(protocol["burned_periods"][0].split("/")[0]), "V1-C", **(external_loader_kwargs or {}),
        )

    comparator_features, comparator_manifest = build_prediction_features_v1(
        prepared, "V1-A", cutoff=pd.Timestamp(protocol["sealed_test"].split("/")[0] if final_test else protocol["burned_periods"][0].split("/")[0]),
    )
    comparator_features = comparator_features.reset_index(drop=True)
    candidate_metrics: dict[str, Any] = {}
    all_predictions: dict[int, list[pd.DataFrame]] = {horizon: [] for horizon in HORIZONS}
    manifests: dict[str, list[str]] = {}
    for candidate in CANDIDATES:
        if candidate == "V1-A":
            features, manifest = comparator_features.copy(), comparator_manifest
        else:
            features, manifest = build_prediction_features_v1(
                prepared, candidate, cutoff=pd.Timestamp(protocol["sealed_test"].split("/")[0] if final_test else protocol["burned_periods"][0].split("/")[0]), external_features=external_features,
                external_loader_kwargs=external_loader_kwargs,
            )
        features = features.reset_index(drop=True)
        manifests[candidate] = manifest
        candidate_metrics[candidate] = {}
        if smoke:
            split = _smoke_split(targets)
            for horizon in HORIZONS:
                result = _run_split(features, targets, split, horizon, candidate=candidate, label="smoke", bootstrap=draws, n_estimators=40, comparator_features=comparator_features)
                candidate_metrics[candidate][f"smoke_h{horizon}"] = result["metrics"]
                all_predictions[horizon].append(result["predictions"])
            fold_dates = [{"name": "smoke", "train_end": str(split["train_date"]), "calibration_end": str(split["calibration_date"])}]
        elif final_test:
            fold_dates = []
            for horizon in HORIZONS:
                split = apply_final_split(targets, horizon)
                result = _run_split(features, targets, split, horizon, candidate=candidate, label="final", bootstrap=draws, n_estimators=350, comparator_features=comparator_features)
                candidate_metrics[candidate][f"final_h{horizon}"] = result["metrics"]
                all_predictions[horizon].append(result["predictions"])
        else:
            fold_dates = [fold.as_dict() for fold in intraday_folds()]
            for fold in intraday_folds():
                for horizon in HORIZONS:
                    split = apply_fold(targets, fold, horizon)
                    key = f"{fold.name}_h{horizon}"
                    if any(len(split[name]) < 3 for name in ("train", "calibration", "oos")):
                        candidate_metrics[candidate][key] = {"status": "not_run", "rows": {name: len(split[name]) for name in ("train", "calibration", "oos")}}
                        continue
                    result = _run_split(features, targets, split, horizon, candidate=candidate, label=fold.name, bootstrap=draws, n_estimators=350, comparator_features=comparator_features)
                    candidate_metrics[candidate][key] = result["metrics"]
                    all_predictions[horizon].append(result["predictions"])

    prediction_paths = []
    out_dir.mkdir(parents=True, exist_ok=True)
    for horizon in HORIZONS:
        prediction = pd.concat(all_predictions[horizon], ignore_index=True) if all_predictions[horizon] else pd.DataFrame()
        path = out_dir / f"predictions_h{horizon}.parquet"
        prediction.to_parquet(path, index=False)
        prediction_paths.append(str(path))
    gates = _development_gates(candidate_metrics, protocol) if not final_test and not smoke else None
    fold_payload = {"mode": "smoke" if smoke else "final" if final_test else "dev", "candidates": candidate_metrics, "development_gates": gates}
    (out_dir / "fold_metrics.json").write_text(json.dumps(fold_payload, indent=2, sort_keys=True, default=_json_default) + "\n", encoding="utf-8")
    ablation = {candidate: {"production": candidate == "V1-C", "metrics": candidate_metrics[candidate]} for candidate in CANDIDATES}
    (out_dir / "candidate_ablation.json").write_text(json.dumps(ablation, indent=2, sort_keys=True, default=_json_default) + "\n", encoding="utf-8")
    registration_hash_current = _sha256_file(REPO_ROOT / "registrations/intraday-pred-v1/registration.md")
    manifest_payload = {
        "artifact_version": "intraday-pred-v1",
        "mode": "smoke" if smoke else "final" if final_test else "dev",
        "source_hashes": {"c1": data_hash or _frame_hash(prepared), "external": {name: audit.get("source_sha256") for name, audit in external_audit.items()}},
        "audit_counts": external_audit,
        "feature_manifest": {"path": "registrations/intraday-pred-v1/feature_manifest.json", "sha256": _sha256_file(REPO_ROOT / "registrations/intraday-pred-v1/feature_manifest.json"), "candidates": manifests},
        "model_params": {"horizons": list(HORIZONS), "candidates": list(CANDIDATES), "n_estimators": 40 if smoke else 350, "num_threads": 8},
        "package_versions": _package_versions(),
        "seed": int(protocol.get("seed", SEED)),
        "fold_dates": fold_dates,
        "registration_hash": registration_hash_current,
        "dev_gates": gates,
        "unseal_event": {"unsealed": True, "timestamp_utc": datetime.now(timezone.utc).isoformat()} if final_test else None,
        "row_count": int(len(prepared)),
        "session_count": int(prepared["datetime"].dt.normalize().nunique()),
        "determinism_excluded_fields": ["runtime_metadata", "unseal_event.timestamp_utc"],
        "runtime_metadata": {"generated_at_utc": datetime.now(timezone.utc).isoformat()},
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest_payload, indent=2, sort_keys=True, default=_json_default) + "\n", encoding="utf-8")
    report = _report(candidate_metrics, manifest_payload)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    return {"fold_metrics": fold_payload, "manifest": manifest_payload, "prediction_paths": prediction_paths, "report": report}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    protocol = load_evaluation_protocol()
    out_dir = args.out_dir
    if args.final_test:
        # This check intentionally happens before any C1 read or external adapter call.
        _assert_final_authorized(out_dir, unseal=args.unseal, registration_hash=args.registration_hash)
    frame, data_hash = load_cutoff_safe(args.data_path)
    if args.smoke:
        burned_start = pd.Timestamp(protocol["burned_periods"][0].split("/")[0])
        frame = frame.loc[pd.to_datetime(frame["datetime"]) < burned_start].copy()
        return_code = run_experiment(frame, out_dir, bootstrap=args.bootstrap, smoke=True, data_hash=data_hash)
    elif args.final_test:
        # Final execution is implemented but deliberately not invoked by this task.
        return_code = run_experiment(frame, out_dir, bootstrap=args.bootstrap, final_test=True, unseal=args.unseal, registration_hash=args.registration_hash, data_hash=data_hash)
    else:
        burned_start = pd.Timestamp(load_evaluation_protocol()["burned_periods"][0].split("/")[0])
        frame = frame.loc[pd.to_datetime(frame["datetime"]) < burned_start].copy()
        return_code = run_experiment(frame, out_dir, bootstrap=args.bootstrap, data_hash=data_hash)
    return 0 if return_code is not None else 0


def _run_split(features: pd.DataFrame, targets: pd.DataFrame, split: dict[str, pd.DataFrame], horizon: int, *, candidate: str, label: str, bootstrap: int, n_estimators: int, comparator_features: pd.DataFrame | None = None) -> dict[str, Any]:
    train_index, calibration_index, oos_index = split["train"].index, split["calibration"].index, split["oos"].index
    train_mask = targets.index.isin(train_index)
    models = fit_horizon_models(features, targets, train_mask, horizon, n_estimators=n_estimators)
    cal_prediction = predict_horizon(models, features.loc[calibration_index], scale=targets.loc[calibration_index, f"target_h{horizon}_scale"])
    cal_valid = split["calibration"][f"target_h{horizon}_dir"].notna().to_numpy() & split["calibration"][f"target_h{horizon}_normalized_magnitude"].notna().to_numpy()
    calibrator = fit_correctness_calibrator(cal_prediction.loc[cal_valid], split["calibration"].loc[cal_valid, f"target_h{horizon}_dir"], split["calibration"].loc[cal_valid, "trade_date"])
    prediction = predict_horizon(models, features.loc[oos_index], scale=targets.loc[oos_index, f"target_h{horizon}_scale"])
    prediction["confidence_correct"] = calibrate_confidence(calibrator, prediction)
    prediction["datetime"] = targets.loc[oos_index, "datetime"].to_numpy()
    prediction["trade_date"] = targets.loc[oos_index, "trade_date"].to_numpy()
    prediction["horizon_min"] = int(horizon)
    prediction["candidate"] = candidate
    prediction["realized_dir"] = targets.loc[oos_index, f"target_h{horizon}_dir"].to_numpy()
    prediction["realized_abs_move_bps"] = targets.loc[oos_index, f"target_h{horizon}_return_bps"].abs().to_numpy()
    valid = prediction["realized_dir"].notna() & prediction["realized_abs_move_bps"].notna()
    scored = prediction.loc[valid].reset_index(drop=True)
    train_joined = split["train"].join(features, rsuffix="_feature")
    eval_joined = split["oos"].join(features, rsuffix="_feature")
    magnitude_baseline_values = fixed_magnitude_baselines(split["train"], split["oos"], horizon)
    baseline_frame = pd.DataFrame({name: values for name, values in magnitude_baseline_values.items()}).loc[valid.to_numpy()].reset_index(drop=True)
    baseline_map = {name: baseline_frame[name].to_numpy() for name in baseline_frame}
    non_overlap = non_overlapping_decisions(scored, horizon)
    non_overlap_actual = non_overlap["realized_abs_move_bps"].to_numpy(dtype=float)
    non_overlap_model = non_overlap["median_abs_move_bps"].to_numpy(dtype=float)
    if len(non_overlap):
        non_overlap_baselines = {name: values.loc[non_overlap.index].to_numpy(dtype=float) for name, values in baseline_frame.items()}
        best_non_overlap = min(non_overlap_baselines.values(), key=lambda values: float(np.mean(np.abs(non_overlap_actual - values))))
    else:
        best_non_overlap = np.array([])
    missing_source = _missing_source(features.loc[oos_index].loc[valid.to_numpy()])
    metrics = {
        "status": "ok", "candidate": candidate, "label": label, "rows": {name: int(len(value)) for name, value in split.items() if isinstance(value, pd.DataFrame)},
        "direction": direction_metrics(scored["realized_dir"], scored),
        "magnitude": magnitude_metrics(scored["realized_abs_move_bps"], scored["median_abs_move_bps"], timestamps=scored["datetime"], sessions=scored["trade_date"], regimes=_regimes(features.loc[oos_index], valid), missing_source=missing_source, baseline_predictions=baseline_map, non_overlapping=(non_overlap_actual, non_overlap_model, best_non_overlap), bootstrap_draws=bootstrap),
        "confidence": __import__("evidence.intraday_metrics_v1", fromlist=["confidence_metrics"]).confidence_metrics(correctness_labels(scored, scored["realized_dir"]), scored["confidence_correct"]),
        "multiclass_brier": multiclass_brier(scored["realized_dir"], scored),
        "baselines": {},
    }
    baseline_columns = [f"target_h{horizon}_dir", f"target_h{horizon}_return_bps"]
    train_valid = split["train"][baseline_columns].notna().all(axis=1)
    train_baseline_rows = pd.concat([split["train"].loc[train_valid, baseline_columns], features.loc[train_index].loc[train_valid]], axis=1)
    eval_baseline_rows = pd.concat([split["oos"][baseline_columns], features.loc[oos_index]], axis=1).loc[valid.to_numpy()]
    direction_baseline_rows = direction_baselines(
        train_baseline_rows,
        eval_baseline_rows,
        horizon,
        feature_columns=features.columns,
        comparator_features=comparator_features,
    )
    for name, baseline_prediction in direction_baseline_rows.items():
        metrics["baselines"][name] = {
            "direction": direction_metrics(scored["realized_dir"], baseline_prediction),
            "magnitude": {"mae_bps": float(np.mean(np.abs(scored["realized_abs_move_bps"].to_numpy() - baseline_map.get("zero_magnitude", 0.0))))},
        }
    return {"predictions": scored, "metrics": metrics}


def _smoke_split(targets: pd.DataFrame) -> dict[str, pd.DataFrame]:
    sessions = pd.Index(pd.to_datetime(targets["trade_date"]).dt.normalize().drop_duplicates()).sort_values()
    if len(sessions) < 3:
        raise ValueError("smoke run needs at least three sessions")
    train_end = sessions[max(0, int(len(sessions) * 0.50) - 1)]
    calibration_end = sessions[max(1, int(len(sessions) * 0.67) - 1)]
    dates = pd.to_datetime(targets["trade_date"]).dt.normalize()
    return {"train": targets.loc[dates <= train_end], "calibration": targets.loc[(dates > train_end) & (dates <= calibration_end)], "oos": targets.loc[dates > calibration_end], "train_date": train_end, "calibration_date": calibration_end}


def _development_gates(candidate_metrics: dict[str, Any], protocol: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for horizon in HORIZONS:
        candidate_blocks = candidate_metrics.get("V1-C", {})
        rows = [candidate_blocks[key] for fold in range(1, 6) if (key := f"fold_{fold}_h{horizon}") in candidate_blocks and candidate_blocks[key].get("status") == "ok"]
        if not rows:
            result[f"H{horizon}"] = {"status": "failed", "passed": False, "checks": {"data": {"passed": False, "reason": "no completed folds"}}}
            continue
        direction = {"pooled_balanced_accuracy": _weighted(rows, "direction", "balanced_accuracy"), "pooled_macro_auc": _weighted(rows, "direction", "macro_ovr_auc"), "fold_balanced_accuracies": [row["direction"]["balanced_accuracy"] for row in rows]}
        magnitude = {"relative_mae_skill": _weighted(rows, "magnitude", "relative_mae_skill"), "positive_skill_folds": sum((row["magnitude"].get("relative_mae_skill") or -1) > 0 for row in rows), "daily_spearman_ic": {"mean": _mean_nested(rows, "magnitude", "daily_spearman_ic", "mean"), "positive_fraction": _mean_nested(rows, "magnitude", "daily_spearman_ic", "positive_fraction")}, "non_overlapping_mae_skill": _mean_nested(rows, "magnitude", "non_overlapping_mae_skill")}
        result[f"H{horizon}"] = development_gate_verdicts({"direction": direction, "magnitude": magnitude, "confidence": _mean_confidence(rows)}, protocol=protocol, horizon=horizon)
    return result


def _mean_confidence(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {key: float(np.mean([row["confidence"].get(key) for row in rows if row["confidence"].get(key) is not None])) if any(row["confidence"].get(key) is not None for row in rows) else None for key in ("ece", "decile_accuracy_spearman", "top_minus_bottom_decile_accuracy")}


def _weighted(rows: list[dict[str, Any]], section: str, key: str) -> float | None:
    values = [(row[section].get(key), row["rows"].get("oos", 0)) for row in rows if row[section].get(key) is not None]
    return float(np.average([v for v, _ in values], weights=[w for _, w in values])) if values else None


def _mean_nested(rows: list[dict[str, Any]], *keys: str) -> float | None:
    values = []
    for row in rows:
        value: Any = row
        for key in keys:
            value = value.get(key) if isinstance(value, dict) else None
        if value is not None:
            values.append(float(value))
    return float(np.mean(values)) if values else None


def _assert_final_authorized(out_dir: Path, *, unseal: bool, registration_hash: str | None) -> None:
    if not unseal:
        raise PermissionError("final mode requires --unseal")
    expected = _sha256_file(REPO_ROOT / "registrations/intraday-pred-v1/registration.md")
    if registration_hash != expected:
        raise PermissionError("final mode requires the current registration hash")
    gates_path = out_dir / "fold_metrics.json"
    if not gates_path.exists():
        raise PermissionError("final mode requires recorded development gates")
    payload = json.loads(gates_path.read_text(encoding="utf-8"))
    gates = payload.get("development_gates") or {}
    if not gates or any(value.get("status") != "passed" for value in gates.values()):
        raise PermissionError("final mode requires all development gates recorded as passed")


def _last_sessions(frame: pd.DataFrame, count: int) -> pd.DataFrame:
    dates = pd.to_datetime(frame["datetime"]).dt.normalize()
    sessions = pd.Index(dates.drop_duplicates()).sort_values()
    selected = set(sessions[-int(count):])
    return frame.loc[dates.isin(selected)].reset_index(drop=True)


def _regimes(features: pd.DataFrame, valid: pd.Series | np.ndarray) -> list[Any] | None:
    if "regime" not in features:
        return None
    return features.loc[valid, "regime"].tolist()


def _missing_source(features: pd.DataFrame) -> list[str]:
    columns = [column for column in ("spot_missing", "bank_missing", "vix_missing") if column in features]
    if not columns:
        return ["unknown"] * len(features)
    return np.where(features[columns].fillna(1).sum(axis=1).to_numpy() > 0, "missing", "complete").tolist()


def _frame_hash(frame: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(frame.sort_values("datetime", kind="stable"), index=False).to_numpy().tobytes()).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _package_versions() -> dict[str, str]:
    names = ("numpy", "pandas", "scikit-learn", "lightgbm", "pyarrow")
    return {name: importlib.metadata.version(name) for name in names if _installed(name)}


def _installed(name: str) -> bool:
    try:
        importlib.metadata.version(name)
        return True
    except importlib.metadata.PackageNotFoundError:
        return False


def _report(metrics: dict[str, Any], manifest: dict[str, Any]) -> str:
    lines = ["# Intraday Prediction V1", "", f"Mode: {manifest['mode']}", f"Rows: {manifest['row_count']} across {manifest['session_count']} sessions.", "", "| Candidate | Block | Status | BA | MAE (bps) | Relative MAE skill |", "|---|---|---:|---:|---:|---:|"]
    for candidate, blocks in metrics.items():
        for name, value in blocks.items():
            if value.get("status") != "ok":
                lines.append(f"| {candidate} | {name} | {value.get('status')} | n/a | n/a | n/a |")
            else:
                lines.append(f"| {candidate} | {name} | ok | {value['direction']['balanced_accuracy']:.4f} | {value['magnitude']['mae_bps']:.4f} | {value['magnitude'].get('relative_mae_skill')!s} |")
    lines.extend(["", "V1-C is the registered production candidate; V1-A and V1-B are diagnostic ablations.", ""])
    return "\n".join(lines)


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


if __name__ == "__main__":
    raise SystemExit(main())
