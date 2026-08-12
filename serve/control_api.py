"""Authenticated internal API for durable control-job submission and status."""
from __future__ import annotations

import hmac
import os
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Header, Response
from fastapi.responses import JSONResponse

from contracts.immutable_json import ImmutableCollisionError
from evidence.research_ledger import LedgerError
from ops.control_jobs import (
    ControlJobError,
    JobNotFound,
    JobRequest,
    OperationConflict,
    UnsupportedJobType,
    control_root,
    job_events,
    job_status,
    research_root,
    submit_job,
)
from serve.control_schemas import (
    JobEventResponse,
    JobStatusResponse,
    JobSubmission,
    JobSubmissionResponse,
    ProblemResponse,
    job_status_response,
)


def create_control_app(
    *, root: str | Path | None = None, research: str | Path | None = None, token: str | None = None
) -> FastAPI:
    control = control_root(root)
    research_root(research)
    expected_token = token if token is not None else os.environ.get("MERIDIAN_WORKER_TOKEN")
    if not expected_token:
        raise RuntimeError("MERIDIAN worker token is not configured")
    app = FastAPI(title="MERIDIAN Control")
    router = APIRouter(prefix="/internal/v1")

    def authenticate(authorization: str | None = Header(default=None)) -> None:
        if authorization is None or not authorization.startswith("Bearer ") or not authorization[7:]:
            raise _AuthenticationError(401, "authentication_required", "a bearer token is required")
        if not hmac.compare_digest(authorization[7:], expected_token):
            raise _AuthenticationError(403, "forbidden", "the bearer token is not authorized")

    @router.get("/health")
    def health() -> dict[str, object]:
        return {"ok": True, "service": "meridian-control"}

    @router.post("/jobs", response_model=JobSubmissionResponse, dependencies=[Depends(authenticate)])
    def create_job(submission: JobSubmission, response: Response) -> JobSubmissionResponse:
        outcome = submit_job(
            JobRequest(
                schema_version=submission.schema_version,
                operation_id=submission.operation_id,
                job_type=submission.job_type,
                requested_by=submission.requested_by,
                parameters=submission.parameters,
            ),
            root=control,
        )
        response.status_code = 201 if outcome["created"] else 200
        return JobSubmissionResponse(**outcome)

    @router.get("/jobs/{job_id}", response_model=JobStatusResponse, dependencies=[Depends(authenticate)])
    def get_job(job_id: str) -> JobStatusResponse:
        return job_status_response(job_status(job_id, root=control))

    @router.get(
        "/jobs/{job_id}/events",
        response_model=list[JobEventResponse],
        dependencies=[Depends(authenticate)],
    )
    def get_job_events(job_id: str) -> list[JobEventResponse]:
        return [
            JobEventResponse(
                sequence=event.sequence,
                event_id=event.event_id,
                event_type=event.event_type,
                attempt=event.attempt,
                recorded_at=event.recorded_at,
                payload=dict(event.payload),
            )
            for event in job_events(job_id, root=control)
        ]

    @app.exception_handler(_AuthenticationError)
    def authentication_error(_request, exc: _AuthenticationError) -> JSONResponse:
        return _problem(exc.status_code, exc.code, exc.message)

    @app.exception_handler(UnsupportedJobType)
    def unsupported_job_type(_request, exc: UnsupportedJobType) -> JSONResponse:
        return _problem(422, "unsupported_job_type", str(exc))

    @app.exception_handler(OperationConflict)
    def operation_conflict(_request, exc: OperationConflict) -> JSONResponse:
        return _problem(409, "operation_conflict", str(exc))

    @app.exception_handler(JobNotFound)
    def job_not_found(_request, exc: JobNotFound) -> JSONResponse:
        return _problem(404, "job_not_found", str(exc))

    @app.exception_handler(ControlJobError)
    def invalid_request(_request, exc: ControlJobError) -> JSONResponse:
        return _problem(422, "invalid_request", str(exc))

    @app.exception_handler(LedgerError)
    @app.exception_handler(ImmutableCollisionError)
    def durability_conflict(_request, exc: Exception) -> JSONResponse:
        return _problem(409, "durability_conflict", str(exc))

    app.include_router(router)
    return app


class _AuthenticationError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _problem(status_code: int, code: str, message: str, job_id: str | None = None) -> JSONResponse:
    body = ProblemResponse(code=code, message=message, retryable=False, job_id=job_id)
    return JSONResponse(status_code=status_code, content=body.model_dump())
