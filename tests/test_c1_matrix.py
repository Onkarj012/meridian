from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from features.c1_matrix import ELIGIBILITY_COLUMNS, MATRIX_COLUMNS, build_c1_matrix
from policy.era_costs import cost_bps_fn_for


FIXTURE = Path(__file__).parent / "fixtures" / "c1_matrix_three_sessions.json"


def _session(day: str, rows: int = 65) -> pd.DataFrame:
    timestamps = pd.date_range(f"{day} 09:15", periods=rows, freq="min")
    close = np.full(rows, 100.0)
    return pd.DataFrame({
        "datetime": timestamps,
        "f_open": close,
        "f_high": close,
        "f_low": close,
        "f_close": close,
        "f_vol": np.arange(1, rows + 1, dtype=float),
        "f_oi": np.full(rows, 1_000.0),
    })


def _fixture_sessions() -> list[pd.DataFrame]:
    fixture = json.loads(FIXTURE.read_text())
    return [_session(item["date"], item["rows"]) for item in fixture["sessions"]]


def _spot(date_key: str) -> dict[str, float]:
    return {
        timestamp.strftime("%Y-%m-%d %H:%M:00"): 99.5
        for timestamp in pd.date_range(f"{date_key} 09:15", periods=375, freq="min")
    }


def _load_cli_module():
    path = Path(__file__).parents[1] / "scripts" / "data" / "build_c1_matrix.py"
    spec = importlib.util.spec_from_file_location("build_c1_matrix_cli", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_golden_three_session_fixture_has_registered_columns_and_flags() -> None:
    matrix = build_c1_matrix(_fixture_sessions(), spot_loader=_spot)

    assert len(matrix) == 195
    assert tuple(matrix.columns) == MATRIX_COLUMNS
    assert set(ELIGIBILITY_COLUMNS).issubset(matrix)
    assert not matrix["regime_eligible"].any()  # fewer than 20 completed prior sessions
    assert matrix.loc[matrix["datetime"].dt.strftime("%H:%M") == "09:44", "is_warmup_eligible"].eq(False).all()
    assert matrix.loc[matrix["datetime"].dt.strftime("%H:%M") == "09:45", "is_time_eligible"].eq(True).all()


def test_nan_policy_keeps_spot_gap_features_nan_without_disqualifying_class_a() -> None:
    matrix = build_c1_matrix(_fixture_sessions(), spot_loader=lambda _date: {})
    post_warmup = matrix["datetime"].dt.strftime("%H:%M") == "10:15"

    assert matrix.loc[post_warmup, ["basis", "basis_chg_30m"]].isna().all().all()
    assert matrix.loc[post_warmup, "class_a_eligible"].all()


def test_label_end_timestamp_uses_exit_offset_and_clamps_to_session_end() -> None:
    matrix = build_c1_matrix([_session("2024-01-02", rows=375)], spot_loader=_spot)
    at_open = matrix.loc[matrix["datetime"].dt.strftime("%H:%M") == "09:15"].iloc[0]
    at_1454 = matrix.loc[matrix["datetime"].dt.strftime("%H:%M") == "14:54"].iloc[0]

    assert at_open.label_end_ts == pd.Timestamp("2024-01-02 10:15")
    assert at_1454.label_end_ts == pd.Timestamp("2024-01-02 15:29")


def test_ineligible_rows_are_materialized_not_filtered() -> None:
    matrix = build_c1_matrix(_fixture_sessions(), spot_loader=_spot)

    assert len(matrix) == 195
    assert (matrix["is_time_eligible"] == False).any()  # noqa: E712
    assert (matrix["is_decision_eligible"] == False).all()


def test_cli_build_is_byte_deterministic(tmp_path: Path) -> None:
    cli = _load_cli_module()
    archive = tmp_path / "archive"
    for day in _fixture_sessions():
        session = pd.Timestamp(day["datetime"].iat[0]).normalize()
        path = archive / str(session.year) / str(session.month) / f"nifty_fut_{session:%d_%m_%Y}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        day.rename(columns={"f_open": "open", "f_high": "high", "f_low": "low", "f_close": "close", "f_vol": "volume", "f_oi": "oi"}).assign(
            date=day["datetime"].dt.strftime("%Y-%m-%d"), time=day["datetime"].dt.strftime("%H:%M:%S")
        )[["date", "time", "open", "high", "low", "close", "oi", "volume"]].to_csv(path, index=False)
    data_root = tmp_path / "data"
    for session in pd.date_range("2024-01-02", periods=3, freq="D"):
        spot_path = data_root / "option_data" / "nifty_data" / "nifty_spot" / str(session.year) / str(session.month) / f"nifty_spot_{session:%d_%m_%Y}.csv"
        spot_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"date": [session.strftime("%Y-%m-%d")] * 65, "time": pd.date_range(session + pd.Timedelta(hours=9, minutes=15), periods=65, freq="min").strftime("%H:%M:%S"), "close": 99.5}).to_csv(spot_path, index=False)
    args = argparse.Namespace(archive_root=archive, data_root=data_root, spot_source="auto", spot_index_path=None,
                              from_date=pd.Timestamp("2024-01-02"), to_date=pd.Timestamp("2024-01-04"), out_dir=tmp_path / "out")

    cli.build(args)
    first = {name: _sha256(args.out_dir / name) for name in ("c1.parquet", "hashes.json", "run_report.json")}
    cli.build(args)
    second = {name: _sha256(args.out_dir / name) for name in first}

    assert first == second
    assert pq.read_table(args.out_dir / "c1.parquet").num_rows == 195


def test_era_costs_drive_net_fields_from_gross_fields() -> None:
    matrix = build_c1_matrix([_session("2024-01-02")], spot_loader=_spot)
    row = matrix.iloc[0]
    expected_cost = cost_bps_fn_for()(pd.Timestamp("2024-01-02").date())

    assert row.cost_bps == pytest.approx(expected_cost)
    assert row.net_return_bps == pytest.approx(row.gross_return_bps - expected_cost)
    assert row.net_return_r == pytest.approx(row.gross_return_r - expected_cost / 30.0)
