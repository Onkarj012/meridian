"""Tests for the bounded control-job handlers."""
from __future__ import annotations

import importlib
import json

import pytest

from ops.control_runner import (
    FatalJobError,
    JobContext,
    run_governance_bootstrap,
    run_intraday_v1_1_smoke,
)


def _context(tmp_path, job_type: str, parameters: dict) -> JobContext:
    return JobContext(
        job_id="job-handler-test",
        job_type=job_type,
        parameters=parameters,
        attempt=1,
        control_root=tmp_path / "control",
        research_root=tmp_path / "research",
        artifacts_dir=tmp_path / "control" / "jobs" / "job-handler-test" / "artifacts",
    )


def _study() -> dict:
    return {
        "study_type": "governance.bootstrap.v1",
        "research_question": "Does the candidate improve net returns?",
        "null_hypothesis": "The candidate does not improve net returns.",
        "universe": {"symbols": ["AAA"]},
        "periods": {"train": ["2024"], "validation": ["2025"]},
        "labels": {"horizon": 5},
        "models": {"candidate": "linear"},
        "controls": {"baseline": "flat"},
        "cost_scenarios": {"base_bps": 8},
        "statistical_family": "bootstrap",
        "multiplicity_method": "holm",
        "primary_metric": "net_return",
        "secondary_metrics": ["drawdown"],
        "pass_gate": {"minimum": 0.0},
        "stop_rule": "Stop after the registered sample.",
        "sealed": False,
        "notes": "handler fixture",
    }


def test_governance_bootstrap_is_immutable_and_idempotent(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source" / "nifty500"
    source.mkdir(parents=True)
    (source / "AAA_minute.csv").write_text(
        "timestamp,open,high,low,close,volume\n2025-01-01T09:15:00+05:30,100,101,99,100.5,10\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MERIDIAN_SOURCE_ROOT_TINY", str(source.parent))
    context = _context(
        tmp_path,
        "governance.bootstrap.v1",
        {"source_alias": "tiny", "study": _study()},
    )

    first = run_governance_bootstrap(context)
    second = run_governance_bootstrap(context)
    specs = list((tmp_path / "research").glob("*/study-spec.json"))

    assert first.summary["study_id"] == second.summary["study_id"]
    assert first.summary["spec_created"] is True
    assert second.summary["spec_created"] is False
    assert len(specs) == 1


def test_intraday_smoke_requires_existing_study_spec(tmp_path) -> None:
    context = _context(
        tmp_path,
        "diagnostic.intraday-v1_1-smoke.v1",
        {"study_id": "missing-study"},
    )

    with pytest.raises(FatalJobError, match="study spec not found; run governance.bootstrap.v1 first"):
        run_intraday_v1_1_smoke(context)


def test_intraday_smoke_writes_monkeypatched_summary(tmp_path, monkeypatch) -> None:
    module = importlib.import_module("scripts.run_intraday_prediction_v1_1")
    calls = []

    def fake_run_smoke():
        calls.append(True)
        return {"status": "SMOKE PASS", "seed": 2025, "candidates": {"S0": {}, "S1": {}}}

    monkeypatch.setattr(module, "run_smoke", fake_run_smoke)
    study_id = "study-ready"
    study_dir = tmp_path / "research" / study_id
    study_dir.mkdir(parents=True)
    (study_dir / "study-spec.json").write_text("{}\n", encoding="utf-8")
    context = _context(
        tmp_path,
        "diagnostic.intraday-v1_1-smoke.v1",
        {"study_id": study_id},
    )

    result = run_intraday_v1_1_smoke(context)
    summary_path = study_dir / "diagnostics" / "intraday-v1_1-smoke" / "summary.json"

    assert calls == [True]
    assert result.summary == {"study_id": study_id, "status": "SMOKE PASS", "seed": 2025, "candidates": 2}
    assert json.loads(summary_path.read_text(encoding="utf-8"))["status"] == "SMOKE PASS"
