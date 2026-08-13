"""Lock-held execution for durable MERIDIAN control jobs."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import os
from pathlib import Path
import re
import time
from typing import Any, Mapping

from contracts.immutable_json import ImmutableCollisionError, write_immutable_json
from contracts.snapshot import build_source_snapshot
from contracts.study_spec import StudySpecDraft, StudySpecError, complete_study_spec, write_study_spec
from evidence.research_ledger import ResearchLedgerWriter
from ops.control_jobs import (
    ControlJobError,
    JobStatus,
    UnsupportedJobType,
    claimable_job_ids,
    events_path,
    job_dir,
    job_status,
    load_request,
    lock_path,
    research_root as resolve_research_root,
    resolve_source_root,
)


class FatalJobError(RuntimeError):
    """Raised when retrying a job cannot produce a valid result."""


class RetryableJobError(RuntimeError):
    """Raised when a transient failure should be retried."""


MAX_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (5, 30, 120)


@dataclass(frozen=True)
class JobContext:
    job_id: str
    job_type: str
    parameters: Mapping[str, Any]
    attempt: int
    control_root: Path
    research_root: Path
    artifacts_dir: Path


@dataclass(frozen=True)
class JobResult:
    summary: Mapping[str, Any]
    artifacts: tuple[Mapping[str, Any], ...]


def run_governance_bootstrap(context: JobContext) -> JobResult:
    expected = {"source_alias", "study"}
    if set(context.parameters) != expected:
        raise FatalJobError("parameters must contain exactly source_alias and study")
    source_alias = context.parameters["source_alias"]
    study_values = context.parameters["study"]
    if not isinstance(source_alias, str) or not isinstance(study_values, Mapping):
        raise FatalJobError("source_alias must be a string and study must be a mapping")
    draft_keys = {field.name for field in fields(StudySpecDraft)} - {"source_snapshot_id", "source_manifest_sha256"}
    if set(study_values) != draft_keys:
        raise FatalJobError("study fields do not match StudySpecDraft")
    source_root = resolve_source_root(source_alias)
    snapshot = build_source_snapshot(source_root, context.research_root / "_source-registry")
    snapshot_path = Path(snapshot["snapshot"])
    snapshot_sha256 = _sha256_file(snapshot_path)
    try:
        draft = StudySpecDraft(
            **dict(study_values),
            source_snapshot_id=snapshot["snapshot_id"],
            source_manifest_sha256=snapshot_sha256,
        )
        spec = complete_study_spec(
            draft,
            repo_root=Path(__file__).resolve().parents[1],
            research_root=context.research_root,
        )
        spec_write = write_study_spec(spec, research_root=context.research_root)
    except (TypeError, ValueError) as exc:
        if isinstance(exc, StudySpecError):
            raise
        raise FatalJobError(f"invalid study fields: {exc}") from exc
    spec_path = Path(spec_write["path"])
    artifacts = (
        _artifact("source_snapshot", snapshot_path, context.research_root),
        _artifact("study_spec", spec_path, context.research_root),
    )
    _append_receipt(
        context,
        event_id=f"{spec.study_id}:study-spec",
        study_id=spec.study_id,
        artifact=artifacts[1],
    )
    return JobResult(
        summary={
            "study_id": spec.study_id,
            "artifact_root": _relative_path(Path(spec.artifact_root), context.research_root),
            "snapshot_id": snapshot["snapshot_id"],
            "selected_source_count": snapshot["selected_source_count"],
            "spec_created": bool(spec_write["created"]),
        },
        artifacts=artifacts,
    )


def run_intraday_v1_1_smoke(context: JobContext) -> JobResult:
    if set(context.parameters) != {"study_id"} or not isinstance(context.parameters.get("study_id"), str):
        raise FatalJobError("parameters must contain exactly one string study_id")
    study_id = context.parameters["study_id"]
    study_path = context.research_root / study_id / "study-spec.json"
    if not study_path.is_file():
        raise FatalJobError("study spec not found; run governance.bootstrap.v1 first")
    try:
        from scripts.run_intraday_prediction_v1_1 import run_smoke
    except ModuleNotFoundError:
        import sys
        repo_root = Path(__file__).resolve().parents[1]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from scripts.run_intraday_prediction_v1_1 import run_smoke
    summary = run_smoke()
    summary_path = context.research_root / study_id / "diagnostics" / "intraday-v1_1-smoke" / "summary.json"
    summary_write = write_immutable_json(summary_path, summary)
    artifact = _artifact("intraday_v1_1_smoke_summary", summary_path, context.research_root)
    _append_receipt(
        context,
        event_id=f"{study_id}:intraday-v1_1-smoke",
        study_id=study_id,
        artifact=artifact,
    )
    return JobResult(
        summary={
            "study_id": study_id,
            "status": summary["status"],
            "seed": summary["seed"],
            "candidates": len(summary["candidates"]),
        },
        artifacts=(dict(artifact, sha256=summary_write["sha256"]),),
    )


JOB_HANDLERS: dict[str, Callable[[JobContext], JobResult]] = {
    "governance.bootstrap.v1": run_governance_bootstrap,
    "diagnostic.intraday-v1_1-smoke.v1": run_intraday_v1_1_smoke,
}


def execute_job(
    job_id: str,
    *,
    root: str | Path,
    research: str | Path | None = None,
    handlers: Mapping[str, Callable[[JobContext], JobResult]] | None = None,
    now: Callable[[], datetime] | None = None,
) -> JobStatus:
    control = Path(root)
    research_path = resolve_research_root(research)
    current_time = now or (lambda: datetime.now(timezone.utc))
    path = lock_path(job_id, root=control)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return job_status(job_id, root=control)
        try:
            status = job_status(job_id, root=control)
            if status.state in {"SUCCEEDED", "FAILED"}:
                return status
            writer = ResearchLedgerWriter(events_path(job_id, root=control))
            if status.state == "RUNNING":
                writer.append(
                    event_id=f"{job_id}:recovered:{status.attempt}",
                    event_type="JOB_RECOVERED",
                    study_id="",
                    job_id=job_id,
                    attempt=status.attempt,
                    payload={"previous_attempt": status.attempt},
                    recorded_at=_utc_now(current_time),
                )
            attempt = status.attempt + 1
            writer.append(
                event_id=f"{job_id}:started:{attempt}",
                event_type="JOB_STARTED",
                study_id="",
                job_id=job_id,
                attempt=attempt,
                payload={},
                recorded_at=_utc_now(current_time),
            )
            request = load_request(job_id, root=control)
            context = JobContext(
                job_id=job_id,
                job_type=request.job_type,
                parameters=request.parameters,
                attempt=attempt,
                control_root=control,
                research_root=research_path,
                artifacts_dir=job_dir(job_id, root=control) / "artifacts",
            )
            try:
                registry = handlers if handlers is not None else JOB_HANDLERS
                handler = registry.get(request.job_type)
                if handler is None:
                    raise UnsupportedJobType(f"unsupported job_type: {request.job_type}")
                result = handler(context)
                result_payload = {
                    "summary": dict(result.summary),
                    "artifacts": [dict(artifact) for artifact in result.artifacts],
                }
                write_immutable_json(job_dir(job_id, root=control) / "result.json", result_payload)
                for artifact in result_payload["artifacts"]:
                    writer.append(
                        event_id=f"{job_id}:artifact:{attempt}:{artifact['role']}",
                        event_type="ARTIFACT_WRITTEN",
                        study_id=str(result_payload["summary"].get("study_id", "")),
                        job_id=job_id,
                        attempt=attempt,
                        payload=artifact,
                        recorded_at=_utc_now(current_time),
                    )
                writer.append(
                    event_id=f"{job_id}:succeeded",
                    event_type="JOB_SUCCEEDED",
                    study_id=str(result_payload["summary"].get("study_id", "")),
                    job_id=job_id,
                    attempt=attempt,
                    payload=result_payload,
                    recorded_at=_utc_now(current_time),
                )
            except Exception as exc:
                _record_failure(writer, job_id, attempt, exc, current_time)
            return job_status(job_id, root=control)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def run_once(
    *,
    root: str | Path,
    research: str | Path | None = None,
    handlers: Mapping[str, Callable[[JobContext], JobResult]] | None = None,
    now: Callable[[], datetime] | None = None,
) -> tuple[JobStatus, ...]:
    current_time = now or (lambda: datetime.now(timezone.utc))
    job_ids = claimable_job_ids(root, now=current_time())
    return tuple(
        execute_job(job_id, root=root, research=research, handlers=handlers, now=current_time)
        for job_id in job_ids
    )


def worker_loop(
    *,
    root: str | Path,
    research: str | Path | None = None,
    interval_seconds: float = 2.0,
    iterations: int | None = None,
) -> None:
    cycles = 0
    while iterations is None or cycles < iterations:
        try:
            statuses = run_once(root=root, research=research)
            for status in statuses:
                print(f"{status.job_id} {status.state} attempt={status.attempt}")
        except Exception:
            pass
        cycles += 1
        if iterations is None or cycles < iterations:
            time.sleep(interval_seconds)


def _record_failure(
    writer: ResearchLedgerWriter,
    job_id: str,
    attempt: int,
    exc: Exception,
    now: Callable[[], datetime],
) -> None:
    fatal = isinstance(
        exc,
        (FatalJobError, UnsupportedJobType, ControlJobError, ImmutableCollisionError, StudySpecError),
    )
    if not fatal and attempt < MAX_ATTEMPTS:
        delay = RETRY_DELAYS_SECONDS[min(attempt - 1, len(RETRY_DELAYS_SECONDS) - 1)]
        not_before = _now(now) + timedelta(seconds=delay)
        writer.append(
            event_id=f"{job_id}:retry:{attempt}",
            event_type="JOB_RETRY_SCHEDULED",
            study_id="",
            job_id=job_id,
            attempt=attempt,
            payload={
                "not_before": not_before.isoformat(),
                "error_type": type(exc).__name__,
                "message": _safe_message(exc),
            },
            recorded_at=_utc_now(now),
        )
        return
    writer.append(
        event_id=f"{job_id}:failed",
        event_type="JOB_FAILED",
        study_id="",
        job_id=job_id,
        attempt=attempt,
        payload={
            "error_type": type(exc).__name__,
            "message": _safe_message(exc),
            "attempt": attempt,
            "retryable": False,
        },
        recorded_at=_utc_now(now),
    )


def _append_receipt(context: JobContext, *, event_id: str, study_id: str, artifact: Mapping[str, Any]) -> None:
    ResearchLedgerWriter(context.research_root / "research-ledger.jsonl").append(
        event_id=event_id,
        event_type="ARTIFACT_WRITTEN",
        study_id=study_id,
        job_id=context.job_id,
        attempt=context.attempt,
        payload=dict(artifact),
    )


def _artifact(role: str, path: Path, root: Path) -> dict[str, str]:
    return {"role": role, "path": _relative_path(path, root), "sha256": _sha256_file(path)}


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise FatalJobError("artifact is outside the research root") from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now(now: Callable[[], datetime]) -> datetime:
    value = now()
    if value.tzinfo is None:
        raise ControlJobError("now must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _utc_now(now: Callable[[], datetime]) -> str:
    return _now(now).isoformat()


def _safe_message(exc: Exception) -> str:
    message = re.sub(r"(?<!\w)/(?:[^\s:]+/?)+", "<path>", str(exc))
    for value in sorted((item for item in os.environ.values() if len(item) >= 4), key=len, reverse=True):
        message = message.replace(value, "<redacted>")
    return message[:1000]
