"""Exactly four focused tests for the V1 external-data contract."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from features.intraday_external import load_intraday_external_features


def _write_sources(tmp_path, *, spot_rows, bank_rows, vix_rows):
    spot = tmp_path / "spot.csv"
    bank = tmp_path / "bank.csv"
    vix = tmp_path / "vix.csv"
    pd.DataFrame(spot_rows, columns=["date", "open", "high", "low", "close", "volume"]).to_csv(spot, index=False)
    pd.DataFrame(bank_rows, columns=["Instrument", "Date", "Time", "Open", "High", "Low", "Close"]).to_csv(bank, index=False)
    pd.DataFrame(vix_rows, columns=["date", "open", "high", "low", "close", "volume"]).to_csv(vix, index=False)
    return spot, bank, vix


def _decision_rows(timestamps):
    timestamps = pd.to_datetime(timestamps)
    return pd.DataFrame({"datetime": timestamps, "f_close": [100.0 + i for i in range(len(timestamps))]})


def test_all_source_schemas_are_normalized(tmp_path) -> None:
    spot, bank, vix = _write_sources(
        tmp_path,
        spot_rows=[["2025-06-02 09:15:00", 100, 101, 99, 100.5, 10], ["2025-06-02 09:16:00", 100.5, 102, 100, 101, 11]],
        bank_rows=[["Banknifty", "02-06-2025", "9:15:00", 200, 201, 199, 200.5], ["Banknifty", "02-06-2025", "9:16:00", 200.5, 202, 200, 201]],
        vix_rows=[["2025-05-30", 10, 11, 9, 10.5, 1], ["2025-06-02 09:15:00", 10.5, 12, 10, 11, 2]],
    )
    result, audit = load_intraday_external_features(
        _decision_rows(["2025-06-02 09:17:00"]), "2025-06-03", "V1-C",
        nifty_path=spot, banknifty_path=bank, vix_path=vix,
    )
    assert result.index.tolist() == [0]
    assert result.loc[0, "spot_log_ret_1m"] == pytest.approx(np.log(101 / 100.5))
    assert result.loc[0, "spot_missing"] == 0
    assert result.loc[0, "bank_missing"] == 0
    assert result.loc[0, "vix_close_l1d"] == pytest.approx(10.5)
    assert set(audit) == {"nifty_spot", "banknifty", "vix"}
    assert audit["banknifty"]["min_accepted_timestamp"] == "2025-06-02 09:15:00"


def test_exact_t_minus_one_alignment_has_no_forward_fill_or_cross_session(tmp_path) -> None:
    spot, bank, vix = _write_sources(
        tmp_path,
        spot_rows=[["2025-06-02 09:15:00", 100, 100, 100, 100, 1], ["2025-06-02 09:17:00", 100, 100, 100, 102, 1], ["2025-06-03 09:15:00", 102, 102, 102, 103, 1]],
        bank_rows=[["Banknifty", "02-06-2025", "9:15:00", 200, 200, 200, 200], ["Banknifty", "02-06-2025", "9:17:00", 200, 200, 200, 202], ["Banknifty", "03-06-2025", "9:15:00", 202, 202, 202, 203]],
        vix_rows=[["2025-05-30", 10, 10, 10, 10, 1]],
    )
    rows = _decision_rows(["2025-06-02 09:17:00", "2025-06-03 09:15:00"])
    result, _ = load_intraday_external_features(rows, "2025-06-04", "V1-B", nifty_path=spot, banknifty_path=bank, vix_path=vix)
    assert result["spot_missing"].tolist() == [1, 1]
    assert result["spot_log_ret_1m"].isna().all()
    assert result["basis_bps_l1"].isna().all()


def test_duplicate_policy_collapses_identical_and_rejects_conflicting(tmp_path) -> None:
    common = [["2025-06-02 09:15:00", 100, 101, 99, 100.5, 10], ["2025-06-02 09:16:00", 100.5, 102, 100, 101, 11]]
    spot, bank, vix = _write_sources(tmp_path, spot_rows=common + [common[1]], bank_rows=[], vix_rows=[])
    _, audit = load_intraday_external_features(_decision_rows(["2025-06-02 09:17:00"]), "2025-06-03", "V1-B", nifty_path=spot, banknifty_path=bank, vix_path=vix)
    assert audit["nifty_spot"]["duplicates_collapsed"] == 1
    conflicting = tmp_path / "conflicting.csv"
    pd.DataFrame(common + [["2025-06-02 09:16:00", 100.5, 102, 100, 999, 11]], columns=["date", "open", "high", "low", "close", "volume"]).to_csv(conflicting, index=False)
    with pytest.raises(ValueError, match="conflicting duplicate"):
        load_intraday_external_features(_decision_rows(["2025-06-02 09:17:00"]), "2025-06-03", "V1-B", nifty_path=conflicting, banknifty_path=bank, vix_path=vix)


def test_vix_previous_day_staleness_and_cutoff_are_enforced(tmp_path) -> None:
    spot, bank, vix = _write_sources(
        tmp_path,
        spot_rows=[], bank_rows=[],
        vix_rows=[["2025-06-02", 10, 11, 9, 10, 1], ["2025-06-03 09:15:00", 20, 21, 19, 20, 1]],
    )
    rows = _decision_rows(["2025-06-03 09:15:00", "2025-06-04 09:15:00", "2025-06-10 09:15:00"])
    result, audit = load_intraday_external_features(rows, "2025-06-03", "V1-C", nifty_path=spot, banknifty_path=bank, vix_path=vix)
    assert result["vix_close_l1d"].iloc[0] == pytest.approx(10)
    assert result["vix_close_l1d"].iloc[1] == pytest.approx(10)
    assert result["vix_missing"].iloc[2] == 1
    assert result["vix_stale_calendar_days"].iloc[2] == 8
    assert audit["vix"]["accepted_rows"] == 1
