from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from features.intraday_external import BANK_FEATURE_COLUMNS, BASIS_FEATURE_COLUMNS, SPOT_FEATURE_COLUMNS, VIX_FEATURE_COLUMNS
from scripts.run_intraday_prediction_v1 import REPO_ROOT, run_experiment


def _frame(sessions: int = 6, minutes: int = 80) -> pd.DataFrame:
    rows = []
    for day in range(sessions):
        start = pd.Timestamp("2024-10-01") + pd.offsets.BDay(day)
        times = pd.date_range(start + pd.Timedelta(hours=9, minutes=15), periods=minutes, freq="min")
        close = 100 + day + np.arange(minutes) * 0.01
        for index, timestamp in enumerate(times):
            rows.append({"datetime": timestamp, "trade_date": timestamp.normalize(), "f_open": close[index], "f_high": close[index] + 0.1, "f_low": close[index] - 0.1, "f_close": close[index], "f_vol": 1.0, "f_oi": 1.0, "realized_vol_30m": 0.1, "regime": "range"})
    return pd.DataFrame(rows)


def _external(length: int) -> pd.DataFrame:
    columns = (*SPOT_FEATURE_COLUMNS, *BASIS_FEATURE_COLUMNS, *BANK_FEATURE_COLUMNS, *VIX_FEATURE_COLUMNS)
    result = pd.DataFrame({column: np.zeros(length, dtype=float) for column in columns})
    for column in ("spot_missing", "basis_missing", "bank_missing", "vix_missing"):
        result[column] = 0
    return result


def _stable_json(path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("runtime_metadata", None)
    return json.dumps(payload, sort_keys=True)


def test_runner_guards_final_mode_and_development_cutoff(tmp_path) -> None:
    frame = _frame(3)
    with pytest.raises(PermissionError, match="--unseal"):
        run_experiment(frame, tmp_path / "final", final_test=True)
    current_hash = hashlib.sha256((REPO_ROOT / "registrations/intraday-pred-v1/registration.md").read_bytes()).hexdigest()
    with pytest.raises(PermissionError, match="registration hash"):
        run_experiment(frame, tmp_path / "final", final_test=True, unseal=True, registration_hash="0" * 64)
    unsafe = frame.copy()
    unsafe.loc[0, "datetime"] = pd.Timestamp("2025-07-01 09:15")
    with pytest.raises(AssertionError, match="firewall"):
        run_experiment(unsafe, tmp_path / "dev", smoke=True, external_features=_external(len(unsafe)))
    assert len(current_hash) == 64


def test_smoke_runs_are_deterministic_and_write_all_candidate_artifacts(tmp_path) -> None:
    frame = _frame()
    external = _external(len(frame))
    first_dir, second_dir = tmp_path / "first", tmp_path / "second"
    run_experiment(frame, first_dir, smoke=True, bootstrap=5, external_features=external)
    run_experiment(frame, second_dir, smoke=True, bootstrap=5, external_features=external)
    assert _stable_json(first_dir / "fold_metrics.json") == _stable_json(second_dir / "fold_metrics.json")
    assert _stable_json(first_dir / "manifest.json") == _stable_json(second_dir / "manifest.json")
    for directory in (first_dir, second_dir):
        for name in ("manifest.json", "fold_metrics.json", "candidate_ablation.json", "report.md", "predictions_h15.parquet", "predictions_h60.parquet"):
            assert (directory / name).exists()
        payload = json.loads((directory / "candidate_ablation.json").read_text(encoding="utf-8"))
        assert set(payload) == {"V1-A", "V1-B", "V1-C"}
