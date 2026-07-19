#!/usr/bin/env python3
"""Deterministic runner for the frozen intraday-pred v1.1-smooth study."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import pickle
import sys
from typing import Any

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from contracts.intraday_targets_v1_1 import make_targets
from evidence.intraday_eval_v1_1 import evaluate_predictions
from features.intraday_ema_v1_1 import (
    FEATURE_MANIFESTS,
    build_prediction_features_v1_1,
    ema_warmup_flag,
)
from models.intraday_confidence import calibrate_confidence
from models.intraday_predictor_v1_1 import (
    SEED,
    fit_confidence_calibrator,
    fit_confidence_thresholds,
    fit_direction_model,
    frozen_classifier_params,
    predict_direction,
)


DATA_PATH = REPO_ROOT / "runs/sleeve-f-c1-matrix/c1.parquet"
FROZEN_DIR = REPO_ROOT / "runs/intraday-pred-v1_1-smooth-frozen"
REGISTRATION_PATH = REPO_ROOT / "registrations/intraday-pred-v1_1-smooth/registration.md"
FIREWALL_CUTOFF = pd.Timestamp("2025-07-01")
FIREWALL_MAX = pd.Timestamp("2025-06-30 15:29:00")
TRAIN_END = pd.Timestamp("2025-03-24 23:59:59")
CALIBRATION_START = pd.Timestamp("2025-04-01")
CALIBRATION_END = pd.Timestamp("2025-06-23 23:59:59")
HORIZONS = (15, 60)
CANDIDATES = ("S0", "S1", "S2", "S3")
READ_COLUMNS = [
    "datetime", "trade_date", "f_open", "f_high", "f_low", "f_close", "f_vol", "f_oi",
    "log_ret_1m", "log_ret_5m", "log_ret_15m", "log_ret_30m", "realized_vol_5m", "realized_vol_15m", "realized_vol_30m",
    "atr_5m", "atr_15m", "atr_30m", "vwap_dev", "vwap_slope_5m", "or_dist_high", "or_dist_low", "or_breakout_up", "or_breakout_dn",
    "oi_chg_1m", "oi_chg_5m", "oi_chg_30m", "oi_long_buildup", "oi_short_buildup", "oi_short_cover", "oi_long_unwind",
    "vol_zscore", "vol_oi_ratio", "basis", "basis_chg_30m", "ema_slope", "consec_bars", "regime",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--freeze", action="store_true")
    modes.add_argument("--score", action="store_true")
    modes.add_argument("--evaluate", action="store_true")
    modes.add_argument("--smoke", action="store_true")
    parser.add_argument("--data-path", type=Path, default=DATA_PATH)
    parser.add_argument("--artifact-dir", type=Path, default=FROZEN_DIR)
    parser.add_argument("--out-path", type=Path, default=None)
    parser.add_argument("--predictions-path", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=Path("runs/intraday-pred-v1_1-smooth-eval"))
    parser.add_argument("--descriptive", action="store_true")
    return parser


def load_cutoff_safe(path: Path = DATA_PATH) -> tuple[pd.DataFrame, str]:
    """Predicate-scan the frozen C1 parquet before materializing rows."""
    schema = pq.read_schema(path)
    columns = [column for column in READ_COLUMNS if column in schema.names]
    required = {"datetime", "f_open", "f_high", "f_low", "f_close"}
    missing = sorted(required.difference(columns))
    if missing:
        raise ValueError(f"intraday input is missing columns: {', '.join(missing)}")
    table = pq.read_table(path, columns=columns, filters=[[("datetime", "<", FIREWALL_CUTOFF)]])
    frame = table.to_pandas()
    if frame.empty:
        raise ValueError("cutoff-safe parquet scan returned no rows")
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="raise")
    assert_firewall(frame)
    return frame.sort_values("datetime", kind="stable").reset_index(drop=True), _frame_hash(frame)


def assert_firewall(frame: pd.DataFrame) -> None:
    if "datetime" not in frame:
        raise ValueError("frame requires datetime")
    values = pd.to_datetime(frame["datetime"], errors="raise")
    if values.empty:
        raise ValueError("firewall check requires at least one row")
    if (values >= FIREWALL_CUTOFF).any() or values.max() > FIREWALL_MAX:
        raise AssertionError(f"intraday v1.1 firewall violated: max {values.max()} > {FIREWALL_MAX}")


def freeze_artifacts(
    frame: pd.DataFrame,
    out_dir: Path = FROZEN_DIR,
    *,
    data_hash: str | None = None,
) -> dict[str, Any]:
    """Train all S0-S3 candidates and write the frozen model artifacts."""
    prepared = _prepare(frame)
    assert_firewall(prepared)
    targets = make_targets(prepared, HORIZONS)
    sessions = pd.to_datetime(prepared["trade_date"]).dt.normalize()
    train_rows = sessions <= TRAIN_END.normalize()
    calibration_rows = (sessions >= CALIBRATION_START.normalize()) & (sessions <= CALIBRATION_END.normalize())
    if not train_rows.any() or not calibration_rows.any():
        raise ValueError("freeze input does not contain both registered training and calibration blocks")

    out_dir.mkdir(parents=True, exist_ok=True)
    threshold_values: dict[str, dict[str, float]] = {}
    artifact_names: list[str] = []
    for candidate in CANDIDATES:
        features, manifest = build_prediction_features_v1_1(prepared, candidate)
        for horizon in HORIZONS:
            label_column = _label_column(candidate, horizon)
            model = fit_direction_model(features, targets, train_rows.to_numpy(), horizon, label_column=label_column)
            calibration_valid = calibration_rows.to_numpy() & targets[label_column].notna().to_numpy()
            cal_prediction = predict_direction(model, features.loc[calibration_valid])
            cal_labels = targets.loc[calibration_valid, label_column]
            cal_sessions = sessions.loc[calibration_valid]
            calibrator = fit_confidence_calibrator(cal_prediction, cal_labels, cal_sessions)
            calibrated = calibrate_confidence(calibrator, cal_prediction)
            thresholds = fit_confidence_thresholds(calibrated)
            key = f"{candidate}_h{horizon}"
            artifact = {"candidate": candidate, "horizon": horizon, "manifest": manifest, "model": model, "calibrator": calibrator, "thresholds": thresholds, "label_column": label_column}
            name = f"{key}.pkl"
            with (out_dir / name).open("wb") as handle:
                pickle.dump(artifact, handle, protocol=5)
            artifact_names.append(name)
            threshold_values[key] = thresholds

    manifest = {
        "artifact_version": "intraday-pred-v1_1-smooth",
        "registration_hash": _sha256_file(REGISTRATION_PATH),
        "source_hashes": {"c1": data_hash or _frame_hash(prepared)},
        "feature_manifests": FEATURE_MANIFESTS,
        "model_params": frozen_classifier_params(),
        "package_versions": _package_versions(),
        "seed": int(SEED),
        "training": {"through": str(TRAIN_END), "embargo": ["2025-03-25", "2025-03-31"]},
        "calibration": {"start": str(CALIBRATION_START), "end": str(CALIBRATION_END), "embargo": ["2025-06-24", "2025-06-30"]},
        "thresholds": threshold_values,
        "artifacts": artifact_names,
        "production_candidate": "S3",
        "horizons": list(HORIZONS),
        "determinism_excluded_fields": ["runtime_metadata"],
        "runtime_metadata": {"generated_at_utc": datetime.now(timezone.utc).isoformat()},
    }
    (out_dir / "manifest.json").write_text(_json(manifest), encoding="utf-8")
    return manifest


def score_frame(frame: pd.DataFrame, artifact_dir: Path = FROZEN_DIR) -> pd.DataFrame:
    """Apply every frozen candidate to new sessions and append predictions."""
    prepared = _prepare(frame)
    manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    result = prepared.copy()
    for candidate in CANDIDATES:
        features, _ = build_prediction_features_v1_1(prepared, candidate)
        warmup = ema_warmup_flag(prepared).to_numpy()
        for horizon in HORIZONS:
            key = f"{candidate}_h{horizon}"
            with (artifact_dir / f"{key}.pkl").open("rb") as handle:
                artifact = pickle.load(handle)
            prediction = predict_direction(artifact["model"], features, calibrator=artifact["calibrator"]).reset_index(drop=True)
            prefix = f"{candidate.lower()}_h{horizon}_"
            for column in ("p_down_raw", "p_flat_raw", "p_up_raw", "direction", "confidence"):
                result[f"{prefix}{column}"] = prediction[column].to_numpy()
            result[f"{prefix}threshold_primary"] = float(artifact["thresholds"]["primary"])
            result[f"{prefix}threshold_secondary"] = float(artifact["thresholds"]["secondary"])
            result[f"{prefix}eligible"] = ~warmup if candidate in {"S1", "S3"} else True
    result.attrs["artifact_version"] = manifest.get("artifact_version")
    return result


def evaluate_scored(
    scored: pd.DataFrame,
    out_dir: Path,
    *,
    descriptive: bool = False,
    s0_predictions: dict[int, pd.DataFrame] | None = None,
) -> dict[str, Any]:
    """Write one mechanical gate report for S3 over scored labelled rows."""
    sessions = _session_count(scored)
    if sessions < 60 and not descriptive:
        raise ValueError(f"evaluate requires at least 60 distinct sessions; received {sessions}; use --descriptive for retrospective diagnostics")
    out_dir.mkdir(parents=True, exist_ok=True)
    reports: dict[str, Any] = {"status": "RETROSPECTIVE/DESCRIPTIVE" if descriptive else "PROSPECTIVE", "sessions": sessions, "candidate": "S3", "horizons": {}}
    for horizon in HORIZONS:
        frame = _prediction_frame(scored, "s3", horizon)
        baseline = _prediction_frame(scored, "s0", horizon) if _has_prediction_frame(scored, "s0", horizon) else (s0_predictions or {}).get(horizon)
        reports["horizons"][f"H{horizon}"] = evaluate_predictions(frame, horizon, s0_predictions=baseline)
    (out_dir / "gate_report.json").write_text(_json(reports), encoding="utf-8")
    return reports


def run_smoke(frame: pd.DataFrame | None = None, out_dir: Path | None = None) -> dict[str, Any]:
    """Run a deterministic synthetic/supplied smoke fit without production artifacts."""
    prepared = _prepare(frame if frame is not None else _synthetic_frame())
    sessions = pd.to_datetime(prepared["trade_date"]).dt.normalize().drop_duplicates().sort_values().tolist()
    if len(sessions) < 3:
        raise ValueError("smoke run needs at least three sessions")
    targets = make_targets(prepared, HORIZONS)
    dates = pd.to_datetime(prepared["trade_date"]).dt.normalize()
    train_end = sessions[max(0, len(sessions) // 2 - 1)]
    cal_end = sessions[max(1, (len(sessions) * 2) // 3 - 1)]
    train_rows = dates <= train_end
    cal_rows = (dates > train_end) & (dates <= cal_end)
    oos_rows = dates > cal_end
    summary: dict[str, Any] = {"status": "SMOKE PASS", "seed": SEED, "candidates": {}}
    for candidate in CANDIDATES:
        features, _ = build_prediction_features_v1_1(prepared, candidate)
        summary["candidates"][candidate] = {}
        for horizon in HORIZONS:
            label = _label_column(candidate, horizon)
            model = fit_direction_model(features, targets, train_rows.to_numpy(), horizon, label_column=label)
            cal_valid = cal_rows.to_numpy() & targets[label].notna().to_numpy()
            cal_prediction = predict_direction(model, features.loc[cal_valid])
            calibrator = fit_confidence_calibrator(model_probabilities(cal_prediction), targets.loc[cal_valid, label], dates.loc[cal_valid])
            scored = predict_direction(model, features.loc[oos_rows], calibrator=calibrator)
            summary["candidates"][candidate][f"H{horizon}"] = {
                "rows": int(len(scored)),
                "direction_counts": {str(key): int(value) for key, value in scored["direction"].value_counts().sort_index().items()},
                "confidence_sum": float(scored["confidence"].sum()),
            }
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "smoke_summary.json").write_text(_json(summary), encoding="utf-8")
    return summary


def model_probabilities(prediction: pd.DataFrame) -> pd.DataFrame:
    return prediction[["p_down_raw", "p_flat_raw", "p_up_raw"]]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.smoke:
        result = run_smoke(out_dir=args.out_dir)
        print(f"{result['status']}: deterministic S0-S3/H15-H60 check complete")
        return 0
    if args.freeze:
        frame, data_hash = load_cutoff_safe(args.data_path)
        manifest = freeze_artifacts(frame, args.artifact_dir, data_hash=data_hash)
        print(f"FROZEN: {len(manifest['artifacts'])} model artifacts written to {args.artifact_dir}")
        return 0
    if args.score:
        input_path = args.data_path
        frame = _read_frame(input_path)
        scored = score_frame(frame, args.artifact_dir)
        out_path = args.out_path or args.out_dir / "scored.parquet"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        scored.to_parquet(out_path, index=False)
        print(f"SCORED: {len(scored)} rows written to {out_path}")
        return 0
    if args.evaluate:
        input_path = args.predictions_path or args.data_path
        scored = _read_frame(input_path)
        report = evaluate_scored(scored, args.out_dir, descriptive=args.descriptive)
        print(f"EVALUATED: {report['status']} across {report['sessions']} sessions")
        return 0
    raise AssertionError("no runner mode selected")


def _label_column(candidate: str, horizon: int) -> str:
    return f"target_h{horizon}_smooth_dir" if candidate in {"S2", "S3"} else f"target_h{horizon}_dir"


def _prepare(frame: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("frame must be a non-empty DataFrame")
    result = frame.copy()
    result["datetime"] = pd.to_datetime(result["datetime"], errors="raise")
    result = result.sort_values("datetime", kind="stable").reset_index(drop=True)
    if "trade_date" not in result:
        result["trade_date"] = result["datetime"].dt.normalize()
    else:
        result["trade_date"] = pd.to_datetime(result["trade_date"], errors="raise").dt.normalize()
    if "realized_vol_30m" not in result:
        result["realized_vol_30m"] = 0.0
    return result


def _read_frame(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def _prediction_frame(scored: pd.DataFrame, prefix: str, horizon: int) -> pd.DataFrame:
    key = f"{prefix}_h{horizon}"
    columns = {"datetime": scored["datetime"], "trade_date": scored.get("trade_date", pd.to_datetime(scored["datetime"]).dt.normalize())}
    for source, target in ((f"{key}_p_down_raw", "p_down_raw"), (f"{key}_p_flat_raw", "p_flat_raw"), (f"{key}_p_up_raw", "p_up_raw"), (f"{key}_direction", "direction"), (f"{key}_confidence", "confidence"), (f"{key}_threshold_primary", "threshold_primary")):
        if source in scored:
            columns[target] = scored[source]
    for target in (f"target_h{horizon}_smooth_dir", f"target_h{horizon}_dir", "realized_smooth_dir", "realized_dir"):
        if target in scored:
            columns[target] = scored[target]
    return pd.DataFrame(columns)


def _has_prediction_frame(scored: pd.DataFrame, prefix: str, horizon: int) -> bool:
    return f"{prefix}_h{horizon}_p_down_raw" in scored


def _session_count(frame: pd.DataFrame) -> int:
    if "trade_date" in frame:
        return int(pd.to_datetime(frame["trade_date"]).dt.normalize().nunique())
    return int(pd.to_datetime(frame["datetime"]).dt.normalize().nunique())


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


def _json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=_json_default) + "\n"


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, (pd.Timestamp,)):
        return value.isoformat()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _synthetic_frame(sessions: int = 6, minutes: int = 80) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for day in range(sessions):
        date = pd.Timestamp("2024-10-01") + pd.offsets.BDay(day)
        times = pd.date_range(date + pd.Timedelta(hours=9, minutes=15), periods=minutes, freq="min")
        close = 100.0 + day + np.sin(np.arange(minutes) / 7.0) * 0.2 + np.arange(minutes) * 0.01
        for index, timestamp in enumerate(times):
            rows.append({"datetime": timestamp, "trade_date": timestamp.normalize(), "f_open": close[index], "f_high": close[index] + 0.1, "f_low": close[index] - 0.1, "f_close": close[index], "f_vol": 1.0, "f_oi": 1.0, "realized_vol_30m": 0.1, "regime": "range"})
    return pd.DataFrame(rows)


if __name__ == "__main__":
    raise SystemExit(main())
