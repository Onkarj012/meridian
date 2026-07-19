#!/usr/bin/env python3
"""Run the frozen V0 intraday prediction experiment."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from contracts.intraday_targets import make_targets
from evidence.intraday_folds import apply_final_split, apply_fold, assert_cutoff_safe, final_split, intraday_folds
from evidence.intraday_metrics import confidence_metrics, direction_metrics, magnitude_metrics, multiclass_brier, naive_baselines, robustness_table, session_block_bootstrap
from features.intraday_prediction import FEATURE_MANIFEST, build_prediction_features
from models.intraday_confidence import calibrate_confidence, correctness_labels, fit_correctness_calibrator
from models.intraday_predictor import SEED, fit_horizon_models, predict_horizon


DATA_PATH = REPO_ROOT / "runs/sleeve-f-c1-matrix/c1.parquet"
DEFAULT_OUT_DIR = Path("runs/intraday-pred-v0")
SMOKE_OUT_DIR = Path("runs/intraday-pred-v0-smoke")
READ_COLUMNS = [
    "datetime", "trade_date", "f_open", "f_high", "f_low", "f_close",
    "log_ret_1m", "log_ret_5m", "log_ret_15m", "log_ret_30m", "realized_vol_5m",
    "realized_vol_15m", "realized_vol_30m", "atr_5m", "atr_15m", "atr_30m", "vwap_dev",
    "vwap_slope_5m", "or_dist_high", "or_dist_low", "or_breakout_up", "or_breakout_dn",
    "oi_chg_1m", "oi_chg_5m", "oi_chg_30m", "oi_long_buildup", "oi_short_buildup",
    "oi_short_cover", "oi_long_unwind", "vol_zscore", "vol_oi_ratio", "basis", "basis_chg_30m",
    "ema_slope", "consec_bars", "regime",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--bootstrap", type=int, default=2_000)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--final-test", action="store_true")
    return parser


def load_cutoff_safe(path: Path = DATA_PATH) -> tuple[pd.DataFrame, str]:
    """Predicate-scan only pre-test rows and project the required columns."""
    schema = pq.read_schema(path)
    columns = [column for column in READ_COLUMNS if column in schema.names]
    missing = sorted(set(READ_COLUMNS).difference(columns))
    if missing:
        raise ValueError(f"intraday input is missing columns: {', '.join(missing)}")
    table = pq.read_table(path, columns=columns, filters=[[('datetime', '<', pd.Timestamp('2025-07-01'))]])
    frame = table.to_pandas()
    if frame.empty:
        raise ValueError("cutoff-safe parquet scan returned no rows")
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="raise")
    maximum = frame["datetime"].max()
    assert maximum <= pd.Timestamp("2025-06-30 15:29"), f"cutoff assertion failed: {maximum}"
    assert_cutoff_safe(frame)
    return frame.sort_values("datetime", kind="stable").reset_index(drop=True), _frame_hash(frame)


def run_experiment(
    frame: pd.DataFrame,
    out_dir: Path,
    *,
    bootstrap: int = 2_000,
    smoke: bool = False,
    final_test: bool = False,
    data_hash: str | None = None,
) -> dict[str, Any]:
    """Run a supplied frame, allowing tests to exercise the runner without parquet."""
    out_dir.mkdir(parents=True, exist_ok=True)
    assert_cutoff_safe(frame)
    prepared = frame.copy()
    prepared["datetime"] = pd.to_datetime(prepared["datetime"], errors="raise")
    prepared = prepared.sort_values("datetime", kind="stable").reset_index(drop=True)
    if smoke:
        sessions = pd.Index(prepared["datetime"].dt.normalize().drop_duplicates()).sort_values()
        prepared = prepared.loc[prepared["datetime"].dt.normalize().isin(sessions[-60:])].reset_index(drop=True)
    targets = make_targets(prepared, (15, 60)).reset_index(drop=True)
    features, manifest = build_prediction_features(prepared)
    features = features.reset_index(drop=True)
    horizon_results: dict[int, list[pd.DataFrame]] = {15: [], 60: []}
    fold_metrics: dict[str, Any] = {}
    if smoke:
        split = _smoke_split(targets)
        for horizon in (15, 60):
            result = _run_split(features, targets, split, horizon, bootstrap=bootstrap, n_estimators=40, label="smoke")
            horizon_results[horizon].append(result["predictions"])
            fold_metrics[f"smoke_h{horizon}"] = result["metrics"]
        fold_dates = [{"name": "smoke", **{key: str(value) for key, value in split.items() if key.endswith("_date")}}]
    else:
        fold_dates = [fold.as_dict() for fold in intraday_folds()]
        for fold in intraday_folds():
            for horizon in (15, 60):
                split = _fold_split(targets, fold, horizon)
                if any(len(split[key]) < 3 for key in ("train", "calibration", "oos")):
                    fold_metrics[f"{fold.name}_h{horizon}"] = {"status": "not_run", "rows": {key: len(value) for key, value in split.items()}}
                    continue
                result = _run_split(features, targets, split, horizon, bootstrap=bootstrap, n_estimators=350, label=fold.name)
                horizon_results[horizon].append(result["predictions"])
                fold_metrics[f"{fold.name}_h{horizon}"] = result["metrics"]
    for horizon in (15, 60):
        prediction = pd.concat(horizon_results[horizon], ignore_index=True) if horizon_results[horizon] else pd.DataFrame()
        prediction.to_parquet(out_dir / f"predictions_h{horizon}.parquet", index=False)
    final_metrics = None
    if final_test:
        final_metrics = {}
        for horizon in (15, 60):
            split = _final_split(targets, horizon)
            result = _run_split(features, targets, split, horizon, bootstrap=bootstrap, n_estimators=40 if smoke else 350, label="final")
            final_metrics[f"h{horizon}"] = result["metrics"]
            result["predictions"].to_parquet(out_dir / f"predictions_h{horizon}_final.parquet", index=False)
        (out_dir / "final_metrics.json").write_text(json.dumps(final_metrics, indent=2, default=_json_default) + "\n", encoding="utf-8")
    (out_dir / "fold_metrics.json").write_text(json.dumps(fold_metrics, indent=2, default=_json_default) + "\n", encoding="utf-8")
    manifest_payload = {
        "feature_manifest": manifest,
        "feature_count": len(manifest),
        "params": {"horizons": [15, 60], "bootstrap": int(bootstrap), "smoke": bool(smoke), "n_estimators": 40 if smoke else 350, "num_threads": 8},
        "seed": SEED,
        "fold_dates": fold_dates,
        "final_split": final_split().as_dict() if final_test else None,
        "data_hash": data_hash or _frame_hash(prepared),
        "row_count": int(len(prepared)),
        "session_count": int(prepared["datetime"].dt.normalize().nunique()),
        "class_proportions": _class_proportions(targets),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest_payload, indent=2, default=_json_default) + "\n", encoding="utf-8")
    report = _report(fold_metrics, final_metrics, manifest_payload)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    return {"fold_metrics": fold_metrics, "final_metrics": final_metrics, "manifest": manifest_payload, "report": report}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.bootstrap < 1:
        raise SystemExit("--bootstrap must be positive")
    out_dir = args.out_dir
    if args.smoke and out_dir == DEFAULT_OUT_DIR:
        out_dir = SMOKE_OUT_DIR
    frame, data_hash = load_cutoff_safe()
    run_experiment(frame, out_dir, bootstrap=50 if args.smoke else args.bootstrap, smoke=args.smoke, final_test=args.final_test, data_hash=data_hash)
    return 0


def _run_split(features: pd.DataFrame, targets: pd.DataFrame, split: dict[str, pd.DataFrame], horizon: int, *, bootstrap: int, n_estimators: int, label: str) -> dict[str, Any]:
    train_index = split["train"].index
    calibration_index = split["calibration"].index
    oos_index = split["oos"].index
    train_mask = targets.index.isin(train_index)
    models = fit_horizon_models(features, targets, train_mask, horizon, n_estimators=n_estimators)
    cal_prediction = predict_horizon(models, features.loc[calibration_index], close=targets.loc[calibration_index, "f_close"])
    calibrator = fit_correctness_calibrator(cal_prediction, split["calibration"][f"target_h{horizon}_dir"], split["calibration"]["trade_date"])
    prediction = predict_horizon(models, features.loc[oos_index], close=targets.loc[oos_index, "f_close"])
    prediction["confidence_correct"] = calibrate_confidence(calibrator, prediction)
    prediction["datetime"] = targets.loc[oos_index, "datetime"].to_numpy()
    prediction["trade_date"] = targets.loc[oos_index, "trade_date"].to_numpy()
    prediction["horizon_min"] = horizon
    prediction["realized_dir"] = targets.loc[oos_index, f"target_h{horizon}_dir"].to_numpy()
    prediction["realized_log_return_bps"] = targets.loc[oos_index, f"target_h{horizon}_mag_bps"].to_numpy()
    valid = prediction["realized_dir"].notna() & prediction["realized_log_return_bps"].notna()
    valid_regimes = features.loc[oos_index].loc[valid.to_numpy(), "regime"].reset_index(drop=True) if "regime" in features else None
    scored = prediction.loc[valid].reset_index(drop=True)
    correct = correctness_labels(scored, scored["realized_dir"])
    direction = direction_metrics(scored["realized_dir"], scored)
    magnitude = magnitude_metrics(scored["realized_log_return_bps"], scored["expected_log_return_bps"], timestamps=scored["datetime"], regimes=valid_regimes)
    raw_confidence = scored[["p_down_raw", "p_flat_raw", "p_up_raw"]].max(axis=1)
    confidence = confidence_metrics(correct, scored["confidence_correct"])
    raw_confidence_metrics = confidence_metrics(correct, raw_confidence)
    ci = session_block_bootstrap(correct, scored["trade_date"], statistic=np.mean, n_bootstrap=bootstrap)
    baselines = {}
    baseline_rows = split["oos"].loc[valid.to_numpy()].join(features, rsuffix="_feature")
    for baseline_name, baseline_prediction in naive_baselines(baseline_rows, horizon, training_mean=float(split["train"][f"target_h{horizon}_mag_bps"].mean())).items():
        baseline_prediction["realized_dir"] = scored["realized_dir"].to_numpy()
        baseline_prediction["realized_log_return_bps"] = scored["realized_log_return_bps"].to_numpy()
        baseline_scored = baseline_prediction.reset_index(drop=True)
        baselines[baseline_name] = {
            "direction": direction_metrics(scored["realized_dir"], baseline_scored),
            "magnitude": magnitude_metrics(scored["realized_log_return_bps"], baseline_scored["expected_log_return_bps"]),
        }
    linear_prediction = predict_horizon(models, features.loc[oos_index], close=targets.loc[oos_index, "f_close"], linear=True).loc[valid.to_numpy()].reset_index(drop=True)
    baselines["linear"] = {
        "direction": direction_metrics(scored["realized_dir"], linear_prediction),
        "magnitude": magnitude_metrics(scored["realized_log_return_bps"], linear_prediction["expected_log_return_bps"]),
    }
    metrics = {"status": "ok", "rows": {key: int(len(value)) for key, value in split.items() if isinstance(value, pd.DataFrame)}, "class_proportions": scored["realized_dir"].value_counts(normalize=True).to_dict(), "direction": direction, "magnitude": magnitude, "confidence": confidence, "confidence_raw": raw_confidence_metrics, "baselines": baselines, "multiclass_brier": multiclass_brier(scored["realized_dir"], scored), "accuracy_ci95": {"lower": ci[0], "upper": ci[1]}, "calibrator": {"method": calibrator.method, "sessions": calibrator.sessions, "rows": calibrator.rows}}
    metrics["robustness_non_overlapping"] = robustness_table(scored, horizon)
    return {"predictions": prediction, "metrics": metrics}


def _smoke_split(targets: pd.DataFrame) -> dict[str, pd.DataFrame]:
    sessions = pd.Index(targets["trade_date"].drop_duplicates()).sort_values()
    if len(sessions) < 3:
        raise ValueError("smoke run needs at least three sessions")
    train_end = sessions[max(0, int(len(sessions) * 0.65) - 1)]
    cal_end = sessions[max(1, int(len(sessions) * 0.82) - 1)]
    dates = pd.to_datetime(targets["trade_date"]).dt.normalize()
    return {"train": targets.loc[dates <= train_end], "calibration": targets.loc[(dates > train_end) & (dates <= cal_end)], "oos": targets.loc[dates > cal_end], "train_date": train_end, "calibration_date": cal_end}


def _fold_split(targets: pd.DataFrame, fold: Any, horizon: int) -> dict[str, pd.DataFrame]:
    return apply_fold(targets, fold, horizon)


def _final_split(targets: pd.DataFrame, horizon: int) -> dict[str, pd.DataFrame]:
    return apply_final_split(targets, horizon)


def _class_proportions(targets: pd.DataFrame) -> dict[str, dict[str, float]]:
    result = {}
    for horizon in (15, 60):
        column = f"target_h{horizon}_dir"
        counts = targets[column].value_counts(normalize=True).reindex(["DOWN", "FLAT", "UP"]).fillna(0)
        result[f"h{horizon}"] = {str(key): float(value) for key, value in counts.items()}
    return result


def _frame_hash(frame: pd.DataFrame) -> str:
    return hashlib.sha256(pd.util.hash_pandas_object(frame.sort_values("datetime", kind="stable"), index=False).to_numpy().tobytes()).hexdigest()


def _report(fold_metrics: dict[str, Any], final_metrics: dict[str, Any] | None, manifest: dict[str, Any]) -> str:
    lines = ["# Intraday Prediction V0", "", f"Rows: {manifest['row_count']} across {manifest['session_count']} sessions.", "", "## Run metrics", "", "| Block | Status | Direction accuracy | Balanced accuracy | Magnitude MAE (bps) | Calibrator |", "|---|---:|---:|---:|---:|---|"]
    for name, value in fold_metrics.items():
        if value.get("status") != "ok":
            lines.append(f"| {name} | {value.get('status')} | n/a | n/a | n/a | n/a |")
        else:
            lines.append(f"| {name} | ok | {value['direction']['accuracy']:.4f} | {value['direction']['balanced_accuracy']:.4f} | {value['magnitude']['mae_bps']:.4f} | {value['calibrator']['method']} |")
    if final_metrics is not None:
        lines.extend(["", "## Final untouched test", "", "| Horizon | Accuracy | Balanced accuracy | MAE (bps) |", "|---|---:|---:|---:|"])
        for name, value in final_metrics.items():
            lines.append(f"| {name} | {value['direction']['accuracy']:.4f} | {value['direction']['balanced_accuracy']:.4f} | {value['magnitude']['mae_bps']:.4f} |")
    lines.extend(["", "V0 uses cutoff-safe C1-derived futures features and the frozen close-to-close targets. Cross-asset features and PSI diagnostics are omitted by design.", ""])
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
