from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from evidence.walkforward import WalkForwardFold
from features.sleeve_f_router import FUTURES_FEATURES
from scripts import run_c1_holdout as holdout
from scripts import run_c1_walkforward as wf


def _args(tmp_path: Path, *, smoke: bool = True, capital: float | None = 1_000_000.0) -> SimpleNamespace:
    return SimpleNamespace(
        matrix_dir=tmp_path / "matrix",
        out_dir=tmp_path / "wf",
        b_artifact=None,
        contract_calendar=None,
        sleeve_capital=capital,
        random_replicates=1,
        smoke=smoke,
    )


def _registration_repo(tmp_path: Path, *, placeholder: bool = True, with_summary: bool = False) -> Path:
    root = tmp_path / "repo"
    (root / "registrations/sleeve-f/c1").mkdir(parents=True)
    capital = "— PLACEHOLDER —" if placeholder else "1000000"
    (root / "registrations/sleeve-f/c1/registration.md").write_text(
        f"# registration\n\n**Sleeve capital: {capital}**\n", encoding="utf-8"
    )
    for name in ("amendments-p0.md", "session-quality-dispositions.md"):
        (root / "registrations/sleeve-f/c1" / name).write_text(f"# {name}\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "registration"], cwd=root, check=True)
    if with_summary:
        wf_dir = root / "runs/sleeve-f-c1-wf"
        wf_dir.mkdir(parents=True)
        (wf_dir / "summary.json").write_text('{"selected_candidate":"A","folds":[]}', encoding="utf-8")
        subprocess.run(["git", "add", str(wf_dir / "summary.json")], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "wf summary"], cwd=root, check=True)
    return root


def _synthetic_matrix(days: int = 4) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for day_index in range(days):
        day = pd.Timestamp("2020-01-02") + pd.Timedelta(days=day_index)
        expiry = day + pd.Timedelta(days=30)
        for bar in range(6):
            timestamp = day + pd.Timedelta(hours=9, minutes=45 + bar)
            row: dict[str, object] = {
                "datetime": timestamp,
                "trade_date": day,
                "f_open": 100.0,
                "f_high": 100.2,
                "f_low": 99.9,
                "f_close": 100.1,
                "front_expiry": expiry,
                "regime": "normal",
                "is_decision_eligible": True,
                "is_session_eligible": True,
                "is_time_eligible": True,
                "is_warmup_eligible": True,
                "class_a_eligible": True,
                "regime_eligible": True,
                "label_window_contiguous": True,
                "label_excluded": False,
                "realized_vol_30m": float(day_index + bar + 1),
                "minute_of_day": timestamp.hour * 60 + timestamp.minute,
                "score_seed": float(10 - bar + day_index),
                "net_label": (bar + day_index) % 2,
                "net_return_r": float((bar + day_index) % 3 - 1),
                "label_end_ts": timestamp + pd.Timedelta(minutes=1),
            }
            for feature_index, feature in enumerate(FUTURES_FEATURES):
                row[feature] = float(feature_index + bar + day_index + 1)
            rows.append(row)
    return pd.DataFrame(rows)


def _fake_unit(*, fold, candidate, samples, unit_dir, **kwargs):
    return {
        "metadata": {"oos_start": fold.oos_start.date().isoformat(), "oos_end": fold.oos_end.date().isoformat()},
        "trades": pd.DataFrame(),
        "daily": pd.DataFrame(columns=["policy_return_bps", "trade_count"]),
        "baselines": {},
        "unit_dir": unit_dir,
    }


def test_firewall_drops_post_cutoff_before_fold_probe(tmp_path, monkeypatch):
    rows = _synthetic_matrix(4)
    rows = pd.concat([rows, rows.iloc[[0]].assign(datetime=pd.Timestamp("2025-07-01"))], ignore_index=True)
    seen: list[tuple[str, pd.Timestamp]] = []

    def probe(label: str, frame: pd.DataFrame) -> None:
        seen.append((label, pd.to_datetime(frame["datetime"]).max()))

    monkeypatch.setattr(wf, "_run_unit", _fake_unit)
    result = wf.run(
        _args(tmp_path),
        matrix=rows,
        folds=[WalkForwardFold("fold_1", pd.Timestamp("2020-01-02"), pd.Timestamp("2020-01-03"), pd.Timestamp("2020-01-04"), pd.Timestamp("2020-01-04"))],
        firewall_probe=probe,
    )
    assert result["matrix"]["rows_after_firewall"] == len(rows) - 1
    assert seen and all(timestamp <= wf.HOLDOUT_CUTOFF for _, timestamp in seen)


def test_placeholder_capital_aborts_non_smoke_run(tmp_path):
    with pytest.raises(wf.PreRunVerificationError, match="PLACEHOLDER"):
        wf.run(_args(tmp_path, smoke=False, capital=None), matrix=_synthetic_matrix(), folds=[])


def test_dirty_registration_file_aborts(tmp_path):
    root = _registration_repo(tmp_path)
    registration = root / "registrations/sleeve-f/c1/registration.md"
    registration.write_text(registration.read_text(encoding="utf-8") + "dirty\n", encoding="utf-8")
    with pytest.raises(wf.PreRunVerificationError, match="not clean"):
        wf.run(_args(tmp_path), root=root, matrix=_synthetic_matrix(), folds=[])


def test_completed_unit_is_skipped_and_byte_identical(tmp_path, monkeypatch):
    unit_dir = tmp_path / "fold_01" / "A"
    unit_dir.mkdir(parents=True)
    (unit_dir / "stable.txt").write_text("deterministic\n", encoding="utf-8")
    wf._mark_unit_complete(unit_dir)
    before = {path.name: path.read_bytes() for path in unit_dir.iterdir()}
    monkeypatch.setattr(wf, "_load_unit", lambda path: {"metadata": {}, "trades": pd.DataFrame(), "daily": pd.DataFrame(), "baselines": {}})
    unit = wf._run_unit(
        fold=WalkForwardFold("fold_1", pd.Timestamp("2020-01-01"), pd.Timestamp("2020-01-02"), pd.Timestamp("2020-01-03"), pd.Timestamp("2020-01-03")),
        candidate="A", samples=None, unit_dir=unit_dir, sleeve_capital=1_000_000.0,
        contract_calendar=pd.DataFrame(), random_replicates=1, b_artifact=None, smoke=True, probe=None,
    )
    after = {path.name: path.read_bytes() for path in unit_dir.iterdir()}
    assert unit["skipped_resume"] is True
    assert before == after


def test_holdout_refusals_without_summary_without_flag_and_placeholder(tmp_path):
    missing_root = _registration_repo(tmp_path / "missing")
    args = holdout.build_parser().parse_args(["--open-holdout", "--yes-i-understand-one-shot"])
    with pytest.raises(holdout.PreRunVerificationError, match="summary does not exist"):
        holdout.run(args, root=missing_root)

    no_flag_root = _registration_repo(tmp_path / "no-flag", placeholder=False, with_summary=True)
    no_flag_args = holdout.build_parser().parse_args([])
    with pytest.raises(holdout.PreRunVerificationError, match="--open-holdout"):
        holdout.run(no_flag_args, root=no_flag_root)

    placeholder_root = _registration_repo(tmp_path / "placeholder", placeholder=True, with_summary=True)
    placeholder_args = holdout.build_parser().parse_args(["--open-holdout", "--yes-i-understand-one-shot"])
    with pytest.raises(holdout.PreRunVerificationError, match="PLACEHOLDER"):
        holdout.run(placeholder_args, root=placeholder_root)


def test_smoke_runs_end_to_end_and_is_labeled(tmp_path, monkeypatch):
    @dataclass
    class FakeTraining:
        model: object
        metadata: dict[str, object]

    monkeypatch.setattr(wf, "train_candidate", lambda candidate, train, validation, *, fold: FakeTraining(object(), {"candidate": candidate, "fold": fold}))
    monkeypatch.setattr(wf, "predict_scores", lambda model, rows, candidate: rows["score_seed"].to_numpy())
    summary = wf.run(_args(tmp_path), matrix=_synthetic_matrix())
    assert summary["status"] == "SMOKE — NOT AN OUTCOME RUN"
    assert summary["candidates"]["A"]["metrics"] == "SUPPRESSED"
    assert (tmp_path / "wf/summary.json").exists()


def test_candidate_b_is_skipped_with_explicit_report_entry(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "_run_unit", _fake_unit)
    summary = wf.run(_args(tmp_path), matrix=_synthetic_matrix())
    assert summary["candidate_B"]["status"] == "SKIPPED"
    assert "open governance item" in summary["candidate_B"]["reason"]
    assert "Candidate B: **SKIPPED**" in (tmp_path / "wf/report.md").read_text(encoding="utf-8")
