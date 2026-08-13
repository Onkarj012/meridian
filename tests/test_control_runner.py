"""Tests for lock-held control-job execution."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import fcntl
import json

from contracts.immutable_json import write_immutable_json
from evidence.research_ledger import ResearchLedgerWriter
from ops.control_jobs import JobRequest, derive_job_id, events_path, job_events, job_status, lock_path, submit_job
from ops.control_runner import (
    MAX_ATTEMPTS,
    FatalJobError,
    JobContext,
    JobResult,
    RetryableJobError,
    execute_job,
    run_once,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _request(operation_id: str = "operation-001") -> JobRequest:
    return JobRequest(
        schema_version="meridian.job-request.v1",
        operation_id=operation_id,
        job_type="diagnostic.intraday-v1_1-smoke.v1",
        requested_by="tester",
        parameters={"study_id": "study-1"},
    )


def _submit(root, operation_id: str = "operation-001") -> str:
    return submit_job(_request(operation_id), root=root)["job_id"]


def _success(_context) -> JobResult:
    return JobResult(
        summary={"ok": True},
        artifacts=({"role": "report", "path": "report.json", "sha256": "a" * 64},),
    )


def test_success_records_lifecycle_and_result(tmp_path) -> None:
    job_id = _submit(tmp_path)

    status = execute_job(job_id, root=tmp_path, research=tmp_path / "research", handlers={_request().job_type: _success})
    events = [json.loads(line) for line in events_path(job_id, root=tmp_path).read_text(encoding="utf-8").splitlines()]

    assert status.state == "SUCCEEDED"
    assert [event["event_type"] for event in events] == [
        "JOB_PLANNED", "JOB_STARTED", "ARTIFACT_WRITTEN", "JOB_SUCCEEDED",
    ]
    result = json.loads((tmp_path / "jobs" / job_id / "result.json").read_text(encoding="utf-8"))
    assert result["summary"] == {"ok": True}


def test_succeeded_job_is_a_no_op(tmp_path) -> None:
    job_id = _submit(tmp_path)
    calls = []

    def handler(context):
        calls.append(context.attempt)
        return _success(context)

    first = execute_job(job_id, root=tmp_path, handlers={_request().job_type: handler})
    original = events_path(job_id, root=tmp_path).read_bytes()
    second = execute_job(job_id, root=tmp_path, handlers={_request().job_type: handler})

    assert first == second
    assert calls == [1]
    assert events_path(job_id, root=tmp_path).read_bytes() == original


def test_retryable_failure_schedules_expected_retry(tmp_path) -> None:
    job_id = _submit(tmp_path)

    def retry(_context):
        raise RetryableJobError("temporary failure")

    status = execute_job(job_id, root=tmp_path, handlers={_request().job_type: retry}, now=lambda: NOW)

    assert status.state == "PENDING"
    assert status.attempt == 1
    assert status.not_before == (NOW + timedelta(seconds=5)).isoformat()
    assert [event.event_type for event in job_events(job_id, root=tmp_path)][-1] == "JOB_RETRY_SCHEDULED"


def test_retry_attempts_exhaust_and_fail(tmp_path) -> None:
    job_id = _submit(tmp_path)

    def retry(_context):
        raise RetryableJobError("still unavailable")

    statuses = [
        execute_job(job_id, root=tmp_path, handlers={_request().job_type: retry}, now=lambda: NOW)
        for _ in range(MAX_ATTEMPTS)
    ]

    assert [status.attempt for status in statuses] == [1, 2, 3]
    assert statuses[-1].state == "FAILED"
    assert statuses[-1].failure["retryable"] is False


def test_fatal_failure_does_not_retry(tmp_path) -> None:
    job_id = _submit(tmp_path)

    def fail(_context):
        raise FatalJobError("invalid input")

    status = execute_job(job_id, root=tmp_path, handlers={_request().job_type: fail})
    event_types = [event.event_type for event in job_events(job_id, root=tmp_path)]

    assert status.state == "FAILED"
    assert "JOB_RETRY_SCHEDULED" not in event_types


def test_unknown_job_type_is_recorded_as_fatal(tmp_path) -> None:
    request = JobRequest("meridian.job-request.v1", "operation-unknown", "unknown.v1", "tester", {})
    job_id = derive_job_id(request)
    directory = tmp_path / "jobs" / job_id
    write_immutable_json(directory / "request.json", {
        "schema_version": request.schema_version, "operation_id": request.operation_id,
        "job_type": request.job_type, "requested_by": request.requested_by, "parameters": {},
    })
    ResearchLedgerWriter(events_path(job_id, root=tmp_path)).append(
        event_id=f"{job_id}:planned", event_type="JOB_PLANNED", study_id="", job_id=job_id,
        attempt=0, payload={},
    )

    status = execute_job(job_id, root=tmp_path, handlers={})

    assert status.state == "FAILED"
    assert status.failure["error_type"] == "UnsupportedJobType"


def test_running_job_is_recovered_on_next_attempt(tmp_path) -> None:
    job_id = _submit(tmp_path)
    ResearchLedgerWriter(events_path(job_id, root=tmp_path)).append(
        event_id=f"{job_id}:started:1", event_type="JOB_STARTED", study_id="", job_id=job_id,
        attempt=1, payload={},
    )

    status = execute_job(job_id, root=tmp_path, handlers={_request().job_type: _success})
    event_types = [event.event_type for event in job_events(job_id, root=tmp_path)]

    assert status.state == "SUCCEEDED"
    assert status.attempt == 2
    assert event_types[2:4] == ["JOB_RECOVERED", "JOB_STARTED"]


def test_busy_lock_returns_unchanged_status(tmp_path) -> None:
    job_id = _submit(tmp_path)
    path = lock_path(job_id, root=tmp_path)
    path.parent.mkdir(parents=True)
    calls = []
    with path.open("a+") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = job_status(job_id, root=tmp_path)
        after = execute_job(
            job_id,
            root=tmp_path,
            handlers={_request().job_type: lambda context: calls.append(context) or _success(context)},
        )

    assert after == before
    assert calls == []


def test_existing_byte_identical_result_is_accepted(tmp_path) -> None:
    job_id = _submit(tmp_path)
    calls = []

    def handler(context):
        calls.append(context.attempt)
        return _success(context)

    first_result = handler(JobContext(
        job_id=job_id,
        job_type=_request().job_type,
        parameters=_request().parameters,
        attempt=1,
        control_root=tmp_path,
        research_root=tmp_path / "research",
        artifacts_dir=tmp_path / "jobs" / job_id / "artifacts",
    ))
    expected = {
        "summary": dict(first_result.summary),
        "artifacts": [dict(artifact) for artifact in first_result.artifacts],
    }
    result_path = tmp_path / "jobs" / job_id / "result.json"
    write_immutable_json(result_path, expected)
    before = result_path.read_bytes()

    status = execute_job(job_id, root=tmp_path, handlers={_request().job_type: handler})

    assert status.state == "SUCCEEDED"
    assert calls == [1, 1]
    assert result_path.read_bytes() == before


def test_run_once_skips_future_retry(tmp_path) -> None:
    ready = _submit(tmp_path, "operation-ready")
    delayed = _submit(tmp_path, "operation-delay")
    ResearchLedgerWriter(events_path(delayed, root=tmp_path)).append(
        event_id=f"{delayed}:retry:0", event_type="JOB_RETRY_SCHEDULED", study_id="", job_id=delayed,
        attempt=0, payload={"not_before": (NOW + timedelta(minutes=1)).isoformat(), "error_type": "X", "message": "later"},
    )

    statuses = run_once(
        root=tmp_path,
        handlers={_request().job_type: _success},
        now=lambda: NOW,
    )

    assert [status.job_id for status in statuses] == [ready]
    assert job_status(delayed, root=tmp_path).state == "PENDING"
