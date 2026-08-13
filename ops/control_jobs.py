"""Durable control-plane job requests and event-projected status."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
from typing import Any, Mapping

from contracts.immutable_json import (
    ImmutableCollisionError,
    canonical_json_text,
    read_immutable_json,
    sha256_json,
    write_immutable_json,
)
from evidence.research_ledger import ResearchEvent, ResearchLedgerWriter, read_events


CONTROL_JOB_FORMAT = "meridian.control-job.v1"
JOB_TYPES = ("governance.bootstrap.v1", "diagnostic.intraday-v1_1-smoke.v1")
_REQUEST_FORMAT = "meridian.job-request.v1"
_OPERATION_ID = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_FORBIDDEN_PARAMETER_KEYS = {
    "path", "out_dir", "output_root", "artifact_root", "root", "command", "module", "script",
}


class ControlJobError(RuntimeError):
    """Raised when a control job violates its durable contract."""


class UnsupportedJobType(ControlJobError):
    """Raised when a request names an unsupported job type."""


class OperationConflict(ControlJobError):
    """Raised when an operation identifier is reused for another request."""


class JobNotFound(ControlJobError):
    """Raised when a requested control job does not exist."""


@dataclass(frozen=True)
class JobRequest:
    schema_version: str
    operation_id: str
    job_type: str
    requested_by: str
    parameters: Mapping[str, Any]


@dataclass(frozen=True)
class JobStatus:
    job_id: str
    job_type: str
    operation_id: str
    state: str
    attempt: int
    not_before: str | None
    created_at: str
    updated_at: str
    result: Mapping[str, Any] | None
    failure: Mapping[str, Any] | None


def control_root(value: str | Path | None = None) -> Path:
    return Path(value or os.environ.get("MERIDIAN_CONTROL_ROOT") or "runs/control")


def research_root(value: str | Path | None = None) -> Path:
    return Path(value or os.environ.get("MERIDIAN_RESEARCH_ROOT") or "data/research")


def resolve_source_root(alias: str, *, environ: Mapping[str, str] | None = None) -> Path:
    if not isinstance(alias, str) or not alias:
        raise ControlJobError("source_alias must be a non-empty string")
    key = "MERIDIAN_SOURCE_ROOT_" + re.sub(r"[^A-Za-z0-9]", "_", alias).upper()
    environment = os.environ if environ is None else environ
    value = environment.get(key)
    if not value:
        raise ControlJobError(f"source root is not configured for alias {alias!r}")
    resolved = Path(value)
    if not resolved.is_dir():
        raise ControlJobError(f"configured source root is not a directory for alias {alias!r}")
    return resolved


def canonical_request(request: JobRequest) -> dict[str, object]:
    return {
        "schema_version": request.schema_version,
        "operation_id": request.operation_id,
        "job_type": request.job_type,
        "requested_by": request.requested_by,
        "parameters": dict(request.parameters),
    }


def request_hash(request: JobRequest) -> str:
    return sha256_json(canonical_request(request))


def derive_job_id(request: JobRequest) -> str:
    payload = {
        "format": CONTROL_JOB_FORMAT,
        "job_type": request.job_type,
        "operation_id": request.operation_id,
        "parameters": dict(request.parameters),
    }
    return "job-" + sha256_json(payload)[:32]


def job_dir(job_id: str, *, root: str | Path) -> Path:
    return Path(root) / "jobs" / job_id


def events_path(job_id: str, *, root: str | Path) -> Path:
    return job_dir(job_id, root=root) / "events.jsonl"


def lock_path(job_id: str, *, root: str | Path) -> Path:
    return Path(root) / "locks" / f"{job_id}.lock"


def submit_job(request: JobRequest, *, root: str | Path) -> dict[str, object]:
    _validate_request(request)
    try:
        request_data = canonical_request(request)
        digest = request_hash(request)
        job_id = derive_job_id(request)
        canonical_json_text(request_data)
    except (TypeError, ValueError) as exc:
        raise ControlJobError(f"request is not canonical JSON: {exc}") from exc
    destination = Path(root)
    claim = {"operation_id": request.operation_id, "request_hash": digest, "job_id": job_id}
    operation_digest = hashlib.sha256(request.operation_id.encode("utf-8")).hexdigest()
    claim_path = destination / "operations" / f"{operation_digest}.json"
    try:
        claim_write = write_immutable_json(claim_path, claim)
    except ImmutableCollisionError as exc:
        raise OperationConflict(f"operation_id already belongs to another request: {request.operation_id}") from exc
    try:
        write_immutable_json(job_dir(job_id, root=destination) / "request.json", request_data)
    except ImmutableCollisionError as exc:
        raise OperationConflict(f"job identity collides with another request: {job_id}") from exc
    ResearchLedgerWriter(events_path(job_id, root=destination)).append(
        event_id=f"{job_id}:planned",
        event_type="JOB_PLANNED",
        study_id="",
        job_id=job_id,
        attempt=0,
        payload={
            "job_type": request.job_type,
            "operation_id": request.operation_id,
            "requested_by": request.requested_by,
            "request_hash": digest,
        },
    )
    return {"job_id": job_id, "created": bool(claim_write["created"]), "state": job_status(job_id, root=destination).state}


def load_request(job_id: str, *, root: str | Path) -> JobRequest:
    path = job_dir(job_id, root=root) / "request.json"
    if not path.is_file():
        raise JobNotFound(f"job not found: {job_id}")
    try:
        value = read_immutable_json(path)
        if not isinstance(value, Mapping):
            raise TypeError("request must contain an object")
        return JobRequest(
            schema_version=value["schema_version"],
            operation_id=value["operation_id"],
            job_type=value["job_type"],
            requested_by=value["requested_by"],
            parameters=value["parameters"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ControlJobError(f"invalid stored job request: {job_id}") from exc


def job_status(job_id: str, *, root: str | Path) -> JobStatus:
    request = load_request(job_id, root=root)
    events = job_events(job_id, root=root)
    if not events:
        raise JobNotFound(f"job not found: {job_id}")
    state = "PENDING"
    not_before = None
    result = None
    failure = None
    for event in events:
        if event.event_type in {"JOB_PLANNED", "JOB_RECOVERED"}:
            state, not_before = "PENDING", None
        elif event.event_type == "JOB_STARTED":
            state, not_before = "RUNNING", None
        elif event.event_type == "JOB_RETRY_SCHEDULED":
            state, not_before = "PENDING", event.payload.get("not_before")
        elif event.event_type == "JOB_SUCCEEDED":
            state, result, failure, not_before = "SUCCEEDED", dict(event.payload), None, None
        elif event.event_type == "JOB_FAILED":
            state, failure, result, not_before = "FAILED", dict(event.payload), None, None
    return JobStatus(
        job_id=job_id,
        job_type=request.job_type,
        operation_id=request.operation_id,
        state=state,
        attempt=max(event.attempt for event in events),
        not_before=not_before,
        created_at=events[0].recorded_at,
        updated_at=events[-1].recorded_at,
        result=result,
        failure=failure,
    )


def job_events(job_id: str, *, root: str | Path) -> tuple[ResearchEvent, ...]:
    directory = job_dir(job_id, root=root)
    if not directory.is_dir():
        raise JobNotFound(f"job not found: {job_id}")
    return read_events(events_path(job_id, root=root))


def claimable_job_ids(root: str | Path, *, now: datetime | None = None) -> tuple[str, ...]:
    jobs_root = Path(root) / "jobs"
    if not jobs_root.is_dir():
        return ()
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ControlJobError("now must be timezone-aware")
    claimable: list[str] = []
    for directory in jobs_root.iterdir():
        if not directory.is_dir():
            continue
        status = job_status(directory.name, root=root)
        if status.state == "RUNNING" or (
            status.state == "PENDING"
            and (status.not_before is None or _parse_utc(status.not_before) <= current.astimezone(timezone.utc))
        ):
            claimable.append(directory.name)
    return tuple(sorted(claimable))


def _validate_request(request: JobRequest) -> None:
    if not isinstance(request, JobRequest):
        raise ControlJobError("request must be a JobRequest")
    if request.schema_version != _REQUEST_FORMAT:
        raise ControlJobError(f"schema_version must equal {_REQUEST_FORMAT}")
    if request.job_type not in JOB_TYPES:
        raise UnsupportedJobType(f"unsupported job_type: {request.job_type}")
    if not isinstance(request.operation_id, str) or _OPERATION_ID.fullmatch(request.operation_id) is None:
        raise ControlJobError("operation_id must be 8..128 characters from [A-Za-z0-9._:-]")
    if not isinstance(request.requested_by, str):
        raise ControlJobError("requested_by must be a string")
    if not isinstance(request.parameters, Mapping):
        raise ControlJobError("parameters must be a mapping")
    _validate_parameters(request.parameters)


def _validate_parameters(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ControlJobError("parameter keys must be strings")
            if key in _FORBIDDEN_PARAMETER_KEYS:
                raise ControlJobError(f"parameter key is forbidden: {key}")
            _validate_parameters(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_parameters(item)


def _parse_utc(value: str) -> datetime:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ControlJobError("job retry timestamp is not valid ISO-8601") from exc
    if timestamp.tzinfo is None:
        raise ControlJobError("job retry timestamp must be timezone-aware")
    return timestamp.astimezone(timezone.utc)
