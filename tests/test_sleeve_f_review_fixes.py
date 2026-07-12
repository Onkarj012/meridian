from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_roll_verdict_records_missing_contract_closes_as_unusable():
    validator = load_script("validate_expiry_join", "runs/sleeve-f-calendar-vix/validate_expiry_join.py")
    row = pd.Series({
        "front_expiry": "2026-07-09", "front_close": float("nan"),
        "next_expiry": "2026-07-16", "next_close": float("nan"),
    })

    verdict, evidence = validator.roll_verdict({"close": 100.0, "oi": None, "time": "15:29:00"}, row, "2026-07-09")

    assert verdict == "unusable"
    assert evidence == {"reason": "front_and_next_close_missing"}


def test_expiry_join_fails_loudly_for_missing_minute_root(tmp_path, monkeypatch):
    validator = load_script("validate_expiry_join_missing_root", "runs/sleeve-f-calendar-vix/validate_expiry_join.py")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "validate_expiry_join.py", "--calendar", str(tmp_path / "calendar.csv"),
            "--expiries", str(tmp_path / "expiries.csv"), "--out-dir", str(tmp_path / "out"),
            "--minute-root", str(tmp_path / "missing"),
        ],
    )

    with pytest.raises(FileNotFoundError, match="minute archive root does not exist"):
        validator.main()


def test_label_parity_skips_empty_csv_and_reports_count(tmp_path):
    parity = load_script("sleeve_f_label_parity", "scripts/data/sleeve_f_label_parity.py")
    columns = "date,time,open,high,low,close\n"
    (tmp_path / "empty.csv").write_text(columns, encoding="utf-8")
    (tmp_path / "zero_bytes.csv").write_text("", encoding="utf-8")
    (tmp_path / "valid.csv").write_text(
        columns + "2024-11-04,09:15:00,1,1,1,1\n", encoding="utf-8"
    )

    frame = parity.load_archive_csvs(tmp_path, pd.Timestamp("2024-11-01"), pd.Timestamp("2024-11-05"))

    assert len(frame) == 1
    assert frame.attrs["skipped_empty_frames"] == 2
