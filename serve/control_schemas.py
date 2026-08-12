"""Pydantic transport models for the internal control API."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ops.control_jobs import JobStatus


class JobSubmission(BaseModel):
    schema_version: str
    operation_id: str
    job_type: str
    requested_by: str
    parameters: dict[str, Any]


class JobSubmissionResponse(BaseModel):
    schema_version: str = "meridian.job-submission.v1"
    job_id: str
    created: bool
    state: str


class JobStatusResponse(BaseModel):
    schema_version: str = "meridian.job-status.v1"
    job_id: str
    job_type: str
    operation_id: str
    state: str
    attempt: int
    not_before: str | None
    created_at: str
    updated_at: str
    result: dict[str, Any] | None
    failure: dict[str, Any] | None


class JobEventResponse(BaseModel):
    sequence: int
    event_id: str
    event_type: str
    attempt: int
    recorded_at: str
    payload: dict[str, Any]


class ProblemResponse(BaseModel):
    code: str
    message: str
    retryable: bool
    job_id: str | None


def job_status_response(status: JobStatus) -> JobStatusResponse:
    return JobStatusResponse(
        job_id=status.job_id,
        job_type=status.job_type,
        operation_id=status.operation_id,
        state=status.state,
        attempt=status.attempt,
        not_before=status.not_before,
        created_at=status.created_at,
        updated_at=status.updated_at,
        result=None if status.result is None else dict(status.result),
        failure=None if status.failure is None else dict(status.failure),
    )
