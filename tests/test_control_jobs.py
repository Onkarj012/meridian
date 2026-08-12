"""Tests for the durable control-job store."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json

import pytest

from evidence.research_ledger import ResearchLedgerWriter
from ops.control_jobs import (
    ControlJobError,
    JobRequest,
    OperationConflict,
    UnsupportedJobType,
    derive_job_id,
    events_path,
    job_status,
    submit_job,
)


def _request(**changes) -> JobRequest:
    values = {
        "schema_version": "meridian.job-request.v1",
        "operation_id": "operation-001",
        "job_type": "diagnostic.intraday-v1_1-smoke.v1",
        "requested_by": "operator-a",
        "parameters": {"study_id": "study-1"},
    }
    values.update(changes)
    return JobRequest(**values)


def test_derive_job_id_is_stable_and_ignores_requested_by() -> None:
    request = _request()

    assert derive_job_id(request) == derive_job_id(request)
    assert derive_job_id(request) == derive_job_id(replace(request, requested_by="operator-b"))


def test_changing_any_parameter_changes_job_id() -> None:
    baseline = derive_job_id(_request(parameters={"study_id": "study-1", "flags": [1, 2]}))

    assert derive_job_id(_request(parameters={"study_id": "study-2", "flags": [1, 2]})) != baseline
    assert derive_job_id(_request(parameters={"study_id": "study-1", "flags": [1, 3]})) != baseline


def test_submit_job_creates_claim_request_and_one_planned_event(tmp_path) -> None:
    request = _request()
    outcome = submit_job(request, root=tmp_path)
    operation_digest = hashlib.sha256(request.operation_id.encode("utf-8")).hexdigest()
    claim = json.loads((tmp_path / "operations" / f"{operation_digest}.json").read_text(encoding="utf-8"))
    request_path = tmp_path / "jobs" / outcome["job_id"] / "request.json"
    events = events_path(outcome["job_id"], root=tmp_path).read_text(encoding="utf-8").splitlines()

    assert outcome == {"job_id": derive_job_id(request), "created": True, "state": "PENDING"}
    assert claim["job_id"] == outcome["job_id"]
    assert json.loads(request_path.read_text(encoding="utf-8"))["requested_by"] == "operator-a"
    assert len(events) == 1
    assert json.loads(events[0])["event_type"] == "JOB_PLANNED"


def test_identical_resubmission_is_idempotent_and_appends_no_event(tmp_path) -> None:
    first = submit_job(_request(), root=tmp_path)
    path = events_path(first["job_id"], root=tmp_path)
    original = path.read_bytes()

    second = submit_job(_request(), root=tmp_path)

    assert second == {"job_id": first["job_id"], "created": False, "state": "PENDING"}
    assert path.read_bytes() == original
    assert len(list((tmp_path / "jobs").iterdir())) == 1


def test_operation_id_reuse_with_different_parameters_conflicts(tmp_path) -> None:
    submit_job(_request(), root=tmp_path)

    with pytest.raises(OperationConflict):
        submit_job(_request(parameters={"study_id": "study-2"}), root=tmp_path)


def test_job_type_schema_and_operation_id_validation(tmp_path) -> None:
    with pytest.raises(UnsupportedJobType):
        submit_job(_request(job_type="unknown.v1"), root=tmp_path)
    with pytest.raises(ControlJobError):
        submit_job(_request(schema_version="wrong"), root=tmp_path)
    with pytest.raises(ControlJobError):
        submit_job(_request(operation_id="bad id"), root=tmp_path)
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


def test_nested_output_directory_parameter_is_rejected(tmp_path) -> None:
    with pytest.raises(ControlJobError):
        submit_job(_request(parameters={"nested": [{"out_dir": "elsewhere"}]}), root=tmp_path)

    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


def test_job_status_projects_all_states_and_highest_attempt(tmp_path) -> None:
    outcome = submit_job(_request(), root=tmp_path)
    job_id = outcome["job_id"]
    writer = ResearchLedgerWriter(events_path(job_id, root=tmp_path))
    assert job_status(job_id, root=tmp_path).state == "PENDING"

    writer.append(
        event_id=f"{job_id}:started:1", event_type="JOB_STARTED", study_id="", job_id=job_id,
        attempt=1, payload={}, recorded_at="2026-01-01T00:00:00+00:00",
    )
    assert job_status(job_id, root=tmp_path).state == "RUNNING"
    writer.append(
        event_id=f"{job_id}:succeeded", event_type="JOB_SUCCEEDED", study_id="", job_id=job_id,
        attempt=1, payload={"summary": {"ok": True}, "artifacts": []},
        recorded_at="2026-01-01T00:01:00+00:00",
    )
    succeeded = job_status(job_id, root=tmp_path)
    assert succeeded.state == "SUCCEEDED"
    assert succeeded.result == {"summary": {"ok": True}, "artifacts": []}
    writer.append(
        event_id=f"{job_id}:failed", event_type="JOB_FAILED", study_id="", job_id=job_id,
        attempt=3, payload={"error_type": "FatalJobError", "message": "failed", "attempt": 3, "retryable": False},
        recorded_at="2026-01-01T00:02:00+00:00",
    )
    failed = job_status(job_id, root=tmp_path)
    assert failed.state == "FAILED"
    assert failed.attempt == 3
    assert failed.failure["message"] == "failed"
