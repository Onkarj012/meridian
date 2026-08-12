"""Tests for the authenticated internal control API."""
from __future__ import annotations

from fastapi.testclient import TestClient

from evidence.research_ledger import ResearchLedgerWriter
from ops.control_jobs import events_path
from serve.control_api import create_control_app


TOKEN = "test-worker-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _submission(**changes) -> dict:
    values = {
        "schema_version": "meridian.job-request.v1",
        "operation_id": "operation-001",
        "job_type": "diagnostic.intraday-v1_1-smoke.v1",
        "requested_by": "api-test",
        "parameters": {"study_id": "study-1"},
    }
    values.update(changes)
    return values


def _client(tmp_path) -> TestClient:
    return TestClient(create_control_app(root=tmp_path, research=tmp_path / "research", token=TOKEN))


def test_authentication_and_public_health(tmp_path) -> None:
    client = _client(tmp_path)

    assert client.get("/internal/v1/health").json() == {"ok": True, "service": "meridian-control"}
    missing = client.post("/internal/v1/jobs", json=_submission())
    wrong = client.post("/internal/v1/jobs", json=_submission(), headers={"Authorization": "Bearer wrong"})

    assert missing.status_code == 401
    assert wrong.status_code == 403


def test_valid_submission_and_idempotent_resubmission(tmp_path) -> None:
    client = _client(tmp_path)

    first = client.post("/internal/v1/jobs", json=_submission(), headers=AUTH)
    second = client.post("/internal/v1/jobs", json=_submission(), headers=AUTH)

    assert first.status_code == 201
    assert first.json()["job_id"].startswith("job-")
    assert second.status_code == 200
    assert second.json()["created"] is False


def test_submission_error_mapping(tmp_path) -> None:
    client = _client(tmp_path)
    unsupported = client.post("/internal/v1/jobs", json=_submission(job_type="unknown.v1"), headers=AUTH)
    client.post("/internal/v1/jobs", json=_submission(), headers=AUTH)
    conflict = client.post(
        "/internal/v1/jobs",
        json=_submission(parameters={"study_id": "study-2"}),
        headers=AUTH,
    )

    assert unsupported.status_code == 422
    assert unsupported.json()["code"] == "unsupported_job_type"
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "operation_conflict"


def test_status_events_and_unknown_job(tmp_path) -> None:
    client = _client(tmp_path)
    created = client.post("/internal/v1/jobs", json=_submission(), headers=AUTH).json()
    job_id = created["job_id"]
    ResearchLedgerWriter(events_path(job_id, root=tmp_path)).append(
        event_id=f"{job_id}:started:1", event_type="JOB_STARTED", study_id="", job_id=job_id,
        attempt=1, payload={},
    )

    status = client.get(f"/internal/v1/jobs/{job_id}", headers=AUTH)
    events = client.get(f"/internal/v1/jobs/{job_id}/events", headers=AUTH)
    missing = client.get("/internal/v1/jobs/job-missing", headers=AUTH)

    assert status.status_code == 200
    assert status.json()["state"] == "RUNNING"
    assert [event["event_type"] for event in events.json()] == ["JOB_PLANNED", "JOB_STARTED"]
    assert missing.status_code == 404
    assert missing.json()["code"] == "job_not_found"


def test_submission_only_enqueues_and_never_executes(tmp_path) -> None:
    client = _client(tmp_path)

    created = client.post("/internal/v1/jobs", json=_submission(), headers=AUTH).json()
    job_id = created["job_id"]
    status = client.get(f"/internal/v1/jobs/{job_id}", headers=AUTH).json()

    assert status["state"] == "PENDING"
    assert not (tmp_path / "jobs" / job_id / "result.json").exists()
