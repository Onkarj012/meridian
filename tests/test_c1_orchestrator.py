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


@pytest.mark.parametrize("placeholder", [True, False])
def test_non_smoke_pre_run_guards_reject_placeholder_or_injected_matrix(tmp_path, placeholder):
    root = _registration_repo(tmp_path, placeholder=placeholder)
    expected = "PLACEHOLDER" if placeholder else "injected matrix"
    with pytest.raises(wf.PreRunVerificationError, match=expected):
        wf.run(_args(tmp_path, smoke=False, capital=None), root=root, matrix=_synthetic_matrix(), folds=[])


def test_dirty_registration_file_aborts(tmp_path):
    root = _registration_repo(tmp_path)
    registration = root / "registrations/sleeve-f/c1/registration.md"
    registration.write_text(registration.read_text(encoding="utf-8") + "dirty\n", encoding="utf-8")
    with pytest.raises(wf.PreRunVerificationError, match="not clean"):
        wf.run(_args(tmp_path), root=root, matrix=_synthetic_matrix(), folds=[])


@pytest.mark.parametrize("mismatch", ["metadata", "marker"])
def test_completed_unit_resume_rejects_metadata_only_or_marker_only_mismatch(tmp_path, monkeypatch, mismatch):
    if mismatch == "metadata":
        unit_dir = tmp_path / "metadata" / "fold_01" / "A"
    else:
        unit_dir = tmp_path / "marker" / "fold_01" / "A"
    unit_dir.mkdir(parents=True)
    (unit_dir / "stable.txt").write_text("deterministic\n", encoding="utf-8")
    (unit_dir / "unit.json").write_text(json.dumps({
        "matrix_sha256": "test-matrix",
        "sleeve_capital": 1_000_000.0,
        "fold_name": "fold_1",
        "candidate": "A",
    }), encoding="utf-8")
    wf._mark_unit_complete(unit_dir)
    marker = json.loads((unit_dir / "COMPLETE.json").read_text(encoding="utf-8"))
    if mismatch == "metadata":
        (unit_dir / "unit.json").write_text(json.dumps({
            "matrix_sha256": "stale-matrix",
            "sleeve_capital": 1_000_000.0,
            "fold_name": "fold_1",
            "candidate": "A",
        }), encoding="utf-8")
        marker["content_hash"] = wf._unit_hash(unit_dir)
    else:
        marker["matrix_sha256"] = "stale-matrix"
    (unit_dir / "COMPLETE.json").write_text(json.dumps(marker), encoding="utf-8")
    monkeypatch.setattr(wf, "_load_unit", lambda path: {"metadata": json.loads((path / "unit.json").read_text(encoding="utf-8")), "trades": pd.DataFrame(), "daily": pd.DataFrame(), "baselines": {}})
    with pytest.raises(wf.PreRunVerificationError, match="stale or incomplete"):
        wf._run_unit(
            fold=WalkForwardFold("fold_1", pd.Timestamp("2020-01-01"), pd.Timestamp("2020-01-02"), pd.Timestamp("2020-01-03"), pd.Timestamp("2020-01-03")),
            candidate="A", samples=None, unit_dir=unit_dir, sleeve_capital=1_000_000.0, matrix_sha256="test-matrix",
            contract_calendar=pd.DataFrame(), random_replicates=1, b_artifact=None, smoke=True, probe=None,
        )


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


def test_load_matrix_rejects_parquet_hash_mismatch(tmp_path):
    matrix_dir = tmp_path / "matrix"
    matrix_dir.mkdir()
    pd.DataFrame({"datetime": [pd.Timestamp("2024-01-01")]}).to_parquet(matrix_dir / "c1.parquet")
    (matrix_dir / "hashes.json").write_text(json.dumps({"sha256": "wrong"}), encoding="utf-8")

    with pytest.raises(wf.PreRunVerificationError, match="sha256 mismatch"):
        wf.load_matrix(matrix_dir)


def test_resume_rejects_completed_unit_without_matching_matrix_hash(tmp_path, monkeypatch):
    unit_dir = tmp_path / "fold_01" / "A"
    unit_dir.mkdir(parents=True)
    (unit_dir / "stable.txt").write_text("deterministic\n", encoding="utf-8")
    (unit_dir / "unit.json").write_text(json.dumps({"candidate": "A"}), encoding="utf-8")
    wf._mark_unit_complete(unit_dir)
    monkeypatch.setattr(wf, "_load_unit", lambda path: {"metadata": {"candidate": "A"}})

    with pytest.raises(wf.PreRunVerificationError, match="stale or incomplete"):
        wf._run_unit(
            fold=WalkForwardFold("fold_1", pd.Timestamp("2020-01-01"), pd.Timestamp("2020-01-02"), pd.Timestamp("2020-01-03"), pd.Timestamp("2020-01-03")),
            candidate="A", samples=None, unit_dir=unit_dir, sleeve_capital=1_000_000.0, matrix_sha256="current-matrix",
            contract_calendar=pd.DataFrame(), random_replicates=1, b_artifact=None, smoke=True, probe=None,
        )


def _holdout_repo_with_summary(tmp_path: Path, summary: dict[str, object]) -> Path:
    root = _registration_repo(tmp_path, placeholder=False, with_summary=True)
    summary_path = root / "runs/sleeve-f-c1-wf/summary.json"
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    subprocess.run(["git", "add", str(summary_path)], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "update wf summary"], cwd=root, check=True)
    return root


def _holdout_args(root: Path, *, candidate: str | None = None, out_dir: Path | None = None) -> SimpleNamespace:
    values = ["--open-holdout", "--yes-i-understand-one-shot"]
    if candidate is not None:
        values += ["--candidate", candidate]
    args = holdout.build_parser().parse_args(values)
    args.matrix_dir = root / "matrix"
    args.wf_dir = Path("runs/sleeve-f-c1-wf")
    args.out_dir = out_dir or root / "holdout"
    return args


def test_holdout_aborts_when_matrix_hash_differs_from_wf_summary(tmp_path):
    root = _holdout_repo_with_summary(tmp_path, {"selected_candidate": "A", "matrix": {"sha256": "wf-hash"}})
    matrix_dir = root / "matrix"
    matrix_dir.mkdir()
    matrix_path = matrix_dir / "c1.parquet"
    matrix_path.write_bytes(b"matrix artifact")
    actual = wf._sha256(matrix_path)
    (matrix_dir / "hashes.json").write_text(json.dumps({"sha256": actual}), encoding="utf-8")
    with pytest.raises(holdout.PreRunVerificationError, match="does not match WF summary"):
        holdout.run(_holdout_args(root), root=root)


def test_holdout_refuses_existing_ledger_or_summary_output(tmp_path):
    root = _holdout_repo_with_summary(tmp_path, {"selected_candidate": "A"})
    out_dir = root / "holdout-a"
    ledger = root / "runs/sleeve-f-c1-holdout/HOLDOUT_OPENED.json"
    ledger.parent.mkdir(parents=True)
    ledger.write_text("{}", encoding="utf-8")
    with pytest.raises(holdout.PreRunVerificationError, match="already spent"):
        holdout.run(_holdout_args(root, out_dir=out_dir), root=root)
    ledger.unlink()
    out_dir.mkdir()
    (out_dir / "summary.json").write_text("{}", encoding="utf-8")
    with pytest.raises(holdout.PreRunVerificationError, match="already spent"):
        holdout.run(_holdout_args(root, out_dir=out_dir), root=root)


def test_holdout_ledger_is_acquired_before_matrix_load_and_remains_on_abort(tmp_path, monkeypatch):
    root = _holdout_repo_with_summary(tmp_path, {"selected_candidate": "A"})
    matrix_dir = root / "matrix"
    matrix_dir.mkdir()
    matrix_path = matrix_dir / "c1.parquet"
    matrix = pd.DataFrame({"datetime": [pd.Timestamp("2025-07-01 09:45")], "f_open": [100.0]})
    matrix.to_parquet(matrix_path)
    matrix_hash = wf._sha256(matrix_path)
    (matrix_dir / "hashes.json").write_text(json.dumps({"sha256": matrix_hash}), encoding="utf-8")
    summary_path = root / "runs/sleeve-f-c1-wf/summary.json"
    summary_path.write_text(json.dumps({"selected_candidate": "A", "matrix": {"sha256": matrix_hash}}), encoding="utf-8")
    subprocess.run(["git", "add", str(summary_path)], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "pin matrix hash"], cwd=root, check=True)

    ledger_path = root / "runs/sleeve-f-c1-holdout/HOLDOUT_OPENED.json"
    seen: list[bool] = []

    def fail_after_ledger(matrix_dir: Path, *, matrix_bytes: bytes):
        seen.append(ledger_path.exists())
        raise holdout.PreRunVerificationError("synthetic matrix-loader abort")

    monkeypatch.setattr(holdout, "load_matrix", fail_after_ledger)
    with pytest.raises(holdout.PreRunVerificationError, match="synthetic matrix-loader abort"):
        holdout.run(_holdout_args(root, out_dir=root / "fresh-out"), root=root)
    assert seen == [True]
    assert ledger_path.exists()


def test_holdout_aborts_when_wf_selected_candidate_is_null_even_with_candidate(tmp_path):
    root = _holdout_repo_with_summary(tmp_path, {"selected_candidate": None})

    with pytest.raises(holdout.PreRunVerificationError, match="no survivor"):
        holdout.run(_holdout_args(root, candidate="A"), root=root)


def test_holdout_aborts_when_candidate_differs_from_wf_selection(tmp_path):
    root = _holdout_repo_with_summary(tmp_path, {"selected_candidate": "B"})
    with pytest.raises(holdout.PreRunVerificationError, match="does not match selected_candidate"):
        holdout.run(_holdout_args(root, candidate="A"), root=root)


def test_holdout_rejects_tampered_frozen_unit_after_completion(tmp_path):
    unit_dir = tmp_path / "wf" / "fold_1" / "B"
    unit_dir.mkdir(parents=True)
    (unit_dir / "unit.json").write_text(json.dumps({"candidate": "B"}), encoding="utf-8")
    (unit_dir / "threshold.json").write_text(json.dumps({"threshold": 0.5}), encoding="utf-8")
    wf._mark_unit_complete(unit_dir)
    (unit_dir / "threshold.json").write_text(json.dumps({"threshold": 0.9}), encoding="utf-8")

    with pytest.raises(holdout.PreRunVerificationError, match="hash validation failed"):
        holdout._load_frozen_decision(tmp_path / "wf", "B", "fold_1")
