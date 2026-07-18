"""Exactly three focused tests for the V1 candidate feature contract."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from features.intraday_external import load_intraday_external_features
from features.intraday_prediction_v1 import (
    FEATURE_MANIFESTS,
    assert_no_forbidden_columns,
    build_prediction_features_v1,
)


def _sources(tmp_path, *, mutate_after: bool = False):
    timestamps = pd.date_range("2025-06-02 09:15", periods=90, freq="min")
    spot_close = 100 + np.arange(len(timestamps), dtype=float) * 0.01
    bank_close = 200 + np.arange(len(timestamps), dtype=float) * 0.04
    spot_rows = [[t, p - 0.01, p + 0.02, p - 0.03, p, 100] for t, p in zip(timestamps, spot_close, strict=True)]
    bank_rows = [["Banknifty", t.strftime("%d-%m-%Y"), t.strftime("%H:%M:%S"), p - 0.01, p + 0.02, p - 0.03, p] for t, p in zip(timestamps, bank_close, strict=True)]
    if mutate_after:
        for row in spot_rows:
            if pd.Timestamp(row[0]) >= pd.Timestamp("2025-06-02 10:16"):
                row[4] += 1000
        for row in bank_rows:
            if pd.Timestamp(f"{row[1]} {row[2]}") >= pd.Timestamp("2025-06-02 10:16"):
                row[7] += 1000
    vix_rows = [[(pd.Timestamp("2025-05-10") + pd.Timedelta(days=i)).date(), 10 + i * 0.1, 10.5 + i * 0.1, 9.5 + i * 0.1, 10 + i * 0.1, 1] for i in range(22)]
    spot = tmp_path / "spot.csv"
    bank = tmp_path / "bank.csv"
    vix = tmp_path / "vix.csv"
    pd.DataFrame(spot_rows, columns=["date", "open", "high", "low", "close", "volume"]).to_csv(spot, index=False)
    pd.DataFrame(bank_rows, columns=["Instrument", "Date", "Time", "Open", "High", "Low", "Close"]).to_csv(bank, index=False)
    pd.DataFrame(vix_rows, columns=["date", "open", "high", "low", "close", "volume"]).to_csv(vix, index=False)
    return spot, bank, vix


def _decisions() -> pd.DataFrame:
    times = pd.date_range("2025-06-02 09:15", periods=90, freq="min")
    return pd.DataFrame({
        "datetime": times,
        "f_open": np.full(len(times), 100.8),
        "f_high": np.full(len(times), 101.0),
        "f_low": np.full(len(times), 100.7),
        "f_close": np.full(len(times), 100.9),
    })


def test_manifest_columns_match_registered_lists_for_all_candidates(tmp_path) -> None:
    manifest_path = "registrations/intraday-pred-v1/feature_manifest.json"
    with open(manifest_path, encoding="utf-8") as handle:
        registered = json.load(handle)["candidates"]
    spot, bank, vix = _sources(tmp_path)
    for candidate in ("V1-A", "V1-B", "V1-C"):
        features, columns = build_prediction_features_v1(
            _decisions(), candidate, cutoff="2025-06-03",
            external_loader_kwargs={"nifty_path": spot, "banknifty_path": bank, "vix_path": vix},
        )
        assert columns == registered[candidate] == list(features.columns)
    assert len(FEATURE_MANIFESTS["V1-A"]) == 42
    assert len(FEATURE_MANIFESTS["V1-B"]) == 54
    assert len(FEATURE_MANIFESTS["V1-C"]) == 71
    assert "basis" not in FEATURE_MANIFESTS["V1-B"]
    assert "basis_chg_30m" not in FEATURE_MANIFESTS["V1-C"]


def test_external_mutations_at_or_after_decision_do_not_change_features(tmp_path) -> None:
    clean_paths = _sources(tmp_path / "clean") if (tmp_path / "clean").mkdir() is None else None
    mutated_paths = _sources(tmp_path / "mutated", mutate_after=True) if (tmp_path / "mutated").mkdir() is None else None
    clean = build_prediction_features_v1(
        _decisions(), "V1-C", cutoff="2025-06-03",
        external_loader_kwargs={"nifty_path": clean_paths[0], "banknifty_path": clean_paths[1], "vix_path": clean_paths[2]},
    )[0]
    mutated = build_prediction_features_v1(
        _decisions(), "V1-C", cutoff="2025-06-03",
        external_loader_kwargs={"nifty_path": mutated_paths[0], "banknifty_path": mutated_paths[1], "vix_path": mutated_paths[2]},
    )[0]
    # The mutation is after decision row 61; later decision rows are expected
    # to change, while the causal row itself and all earlier rows must not.
    assert_frame_equal(clean.iloc[:62], mutated.iloc[:62])


def test_basis_bank_vix_formulas_and_forbidden_guard(tmp_path) -> None:
    spot, bank, vix = _sources(tmp_path)
    decisions = _decisions()
    aligned, _ = load_intraday_external_features(
        decisions, "2025-06-03", "V1-C", nifty_path=spot, banknifty_path=bank, vix_path=vix,
    )
    row = aligned.iloc[61]
    spot_at_end = 100 + 60 * 0.01
    spot_5m_prior = 100 + 55 * 0.01
    bank_at_end = 200 + 60 * 0.04
    bank_5m_prior = 200 + 55 * 0.04
    basis_current = 10_000 * (100.9 / spot_at_end - 1)
    basis_prior = 10_000 * (100.9 / spot_5m_prior - 1)
    basis_window = np.array([10_000 * (100.9 / (100 + i * 0.01) - 1) for i in range(61)])
    assert row["basis_bps_l1"] == pytest.approx(basis_current)
    assert row["basis_delta_5m"] == pytest.approx(basis_current - basis_prior)
    assert row["basis_z_60m"] == pytest.approx((basis_current - basis_window.mean()) / basis_window.std(ddof=1))
    assert row["bank_rel_ret_5m"] == pytest.approx(np.log(bank_at_end / bank_5m_prior) - np.log(spot_at_end / spot_5m_prior))
    assert row["vix_close_l1d"] == pytest.approx(12.1)
    with pytest.raises(AssertionError, match="forbidden"):
        assert_no_forbidden_columns(pd.DataFrame({"entry_price": [100.0]}))
