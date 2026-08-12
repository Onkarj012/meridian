"""Deterministic identity contract for program-wide research studies."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import importlib.metadata
import os
from pathlib import Path
import platform as runtime_platform
import subprocess
from typing import Any, Mapping, Sequence

from contracts.immutable_json import canonical_json_bytes, read_immutable_json, write_immutable_json


STUDY_SPEC_FORMAT = "meridian.study-spec.v1"
DEFAULT_LOCKFILES = ("uv.lock", "control/package-lock.json")
DEFAULT_PACKAGES = (
    "duckdb", "pyarrow", "pandas", "numpy", "lightgbm", "scikit-learn", "fastapi", "pydantic",
)


class StudySpecError(ValueError):
    """Raised when a study identity cannot be constructed or verified."""


@dataclass(frozen=True)
class StudySpecDraft:
    study_type: str
    research_question: str
    null_hypothesis: str
    source_snapshot_id: str
    source_manifest_sha256: str
    universe: Mapping[str, Any]
    periods: Mapping[str, Any]
    labels: Mapping[str, Any]
    models: Mapping[str, Any]
    controls: Mapping[str, Any]
    cost_scenarios: Mapping[str, Any]
    statistical_family: str
    multiplicity_method: str
    primary_metric: str
    secondary_metrics: Sequence[str]
    pass_gate: Mapping[str, Any]
    stop_rule: str
    sealed: bool
    notes: str = ""


@dataclass(frozen=True)
class CodeIdentity:
    head_commit: str
    dirty: bool
    manifest_sha256: str
    file_count: int
    submodules: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class RuntimeIdentity:
    python_version: str
    platform: str
    lockfile_digests: tuple[tuple[str, str], ...]
    package_versions: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class StudySpec:
    format: str
    study_id: str
    draft: StudySpecDraft
    code: CodeIdentity
    runtime: RuntimeIdentity
    model_identity: Mapping[str, Any]
    config_identity: Mapping[str, Any]
    artifact_root: str


def _normalize(value: object, key_path: str) -> object:
    if isinstance(value, Path):
        raise StudySpecError(f"Path value is forbidden in study identity at {key_path}")
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise StudySpecError(f"non-finite float in study identity at {key_path}")
        return value
    if isinstance(value, (set, frozenset)):
        raise StudySpecError(f"set value is forbidden in study identity at {key_path}")
    if isinstance(value, Mapping):
        items = list(value.items())
        for key, _ in items:
            if not isinstance(key, str):
                raise StudySpecError(f"non-string mapping key in study identity at {key_path}: {key!r}")
        normalized: dict[str, object] = {}
        for key, item in sorted(items, key=lambda pair: pair[0]):
            if key in normalized:
                raise StudySpecError(f"duplicate normalized key in study identity at {key_path}: {key!r}")
            normalized[key] = _normalize(item, f"{key_path}.{key}")
        return normalized
    if isinstance(value, Sequence):
        return [_normalize(item, f"{key_path}[{index}]") for index, item in enumerate(value)]
    raise StudySpecError(f"unsupported {type(value).__name__} in study identity at {key_path}")


def _draft_from_normalized(data: Mapping[str, Any]) -> StudySpecDraft:
    try:
        return StudySpecDraft(**dict(data))
    except (TypeError, ValueError) as exc:
        raise StudySpecError(f"invalid study draft: {exc}") from exc


def _code_from_normalized(data: Mapping[str, Any]) -> CodeIdentity:
    try:
        return CodeIdentity(
            head_commit=data["head_commit"],
            dirty=data["dirty"],
            manifest_sha256=data["manifest_sha256"],
            file_count=data["file_count"],
            submodules=tuple(tuple(item) for item in data["submodules"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StudySpecError(f"invalid code identity: {exc}") from exc


def _runtime_from_normalized(data: Mapping[str, Any]) -> RuntimeIdentity:
    try:
        return RuntimeIdentity(
            python_version=data["python_version"],
            platform=data["platform"],
            lockfile_digests=tuple(tuple(item) for item in data["lockfile_digests"]),
            package_versions=tuple(tuple(item) for item in data["package_versions"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StudySpecError(f"invalid runtime identity: {exc}") from exc


def canonical_identity_payload(
    draft: StudySpecDraft,
    code: CodeIdentity,
    runtime: RuntimeIdentity,
    model_identity: Mapping[str, Any],
    config_identity: Mapping[str, Any],
) -> dict[str, object]:
    if not isinstance(draft, StudySpecDraft):
        raise StudySpecError("draft must be a StudySpecDraft")
    if not isinstance(code, CodeIdentity):
        raise StudySpecError("code must be a CodeIdentity")
    if not isinstance(runtime, RuntimeIdentity):
        raise StudySpecError("runtime must be a RuntimeIdentity")
    payload = {
        "format": STUDY_SPEC_FORMAT,
        "draft": asdict(draft),
        "code": asdict(code),
        "runtime": asdict(runtime),
        "model_identity": model_identity,
        "config_identity": config_identity,
    }
    normalized = _normalize(payload, "identity")
    assert isinstance(normalized, dict)
    return normalized


def compute_study_id(
    draft: StudySpecDraft,
    code: CodeIdentity,
    runtime: RuntimeIdentity,
    model_identity: Mapping[str, Any],
    config_identity: Mapping[str, Any],
) -> str:
    payload = canonical_identity_payload(draft, code, runtime, model_identity, config_identity)
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _run_git(repo_root: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            check=False,
            capture_output=True,
            text=False,
        )
    except OSError as exc:
        raise StudySpecError(f"failed to run git: {exc}") from exc
    if result.returncode != 0:
        try:
            detail = result.stderr.decode("utf-8", errors="strict").strip()
        except UnicodeDecodeError as exc:
            raise StudySpecError("git error output is not valid UTF-8") from exc
        raise StudySpecError(f"git {' '.join(args)} failed: {detail or result.returncode}")
    return result.stdout


def _decode_zero_paths(output: bytes, *, command: str) -> list[str]:
    try:
        return [item.decode("utf-8", errors="strict") for item in output.split(b"\0") if item]
    except UnicodeDecodeError as exc:
        raise StudySpecError(f"{command} returned a path that is not valid UTF-8") from exc


def _submodule_identity(repo_root: Path) -> tuple[tuple[str, str], ...]:
    output = _run_git(repo_root, "submodule", "status")
    try:
        lines = output.decode("utf-8", errors="strict").splitlines()
    except UnicodeDecodeError as exc:
        raise StudySpecError("git submodule status returned invalid UTF-8") from exc
    submodules: list[tuple[str, str]] = []
    for line in lines:
        parts = line[1:].split()
        if len(parts) < 2:
            raise StudySpecError(f"could not parse git submodule status line: {line!r}")
        submodules.append((parts[1], parts[0]))
    return tuple(sorted(submodules))


def build_code_identity(repo_root: str | Path) -> CodeIdentity:
    root = Path(repo_root).resolve()
    try:
        head_commit = _run_git(root, "rev-parse", "HEAD").decode("utf-8", errors="strict").strip()
    except UnicodeDecodeError as exc:
        raise StudySpecError("git rev-parse HEAD returned invalid UTF-8") from exc
    tracked = _decode_zero_paths(_run_git(root, "ls-files", "-z"), command="git ls-files")
    untracked = _decode_zero_paths(
        _run_git(root, "ls-files", "-z", "--others", "--exclude-standard"),
        command="git ls-files --others",
    )
    submodules = _submodule_identity(root)
    submodule_paths = {path for path, _ in submodules}
    relative_paths = sorted(set(tracked) | set(untracked))
    digest = hashlib.sha256()
    file_count = 0
    for relative_path in relative_paths:
        if relative_path in submodule_paths:
            continue
        path = root / relative_path
        try:
            if path.is_symlink():
                mode = "120000"
                content = os.readlink(path).encode("utf-8")
            else:
                mode = "100755" if os.access(path, os.X_OK) else "100644"
                content = path.read_bytes()
        except (OSError, UnicodeEncodeError) as exc:
            raise StudySpecError(f"could not read working-tree file {relative_path!r}: {exc}") from exc
        digest.update(f"{relative_path}\0{mode}\0".encode("utf-8"))
        digest.update(content)
        file_count += 1
    dirty = bool(_run_git(root, "status", "--porcelain"))
    return CodeIdentity(head_commit, dirty, digest.hexdigest(), file_count, submodules)


def build_runtime_identity(
    repo_root: str | Path,
    *,
    lockfiles: Sequence[str] = DEFAULT_LOCKFILES,
    packages: Sequence[str] = DEFAULT_PACKAGES,
) -> RuntimeIdentity:
    root = Path(repo_root).resolve()
    lockfile_digests: list[tuple[str, str]] = []
    for relative_value in sorted(lockfiles):
        relative = Path(relative_value)
        if relative.is_absolute():
            raise StudySpecError(f"lockfile path must be relative: {relative_value}")
        path = root / relative
        if path.exists():
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError as exc:
                raise StudySpecError(f"could not read lockfile {relative.as_posix()!r}: {exc}") from exc
            lockfile_digests.append((relative.as_posix(), digest))
    package_versions: list[tuple[str, str]] = []
    for package in sorted(packages):
        try:
            version = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            version = "absent"
        package_versions.append((package, version))
    return RuntimeIdentity(
        runtime_platform.python_version(),
        runtime_platform.platform(),
        tuple(lockfile_digests),
        tuple(package_versions),
    )


def derive_artifact_root(research_root: str | Path, study_id: str) -> Path:
    return Path(research_root) / study_id


def complete_study_spec(
    draft: StudySpecDraft,
    *,
    repo_root: str | Path,
    research_root: str | Path,
    model_identity: Mapping[str, Any] | None = None,
    config_identity: Mapping[str, Any] | None = None,
    code: CodeIdentity | None = None,
    runtime: RuntimeIdentity | None = None,
) -> StudySpec:
    resolved_code = code or build_code_identity(repo_root)
    resolved_runtime = runtime or build_runtime_identity(repo_root)
    payload = canonical_identity_payload(
        draft,
        resolved_code,
        resolved_runtime,
        model_identity or {},
        config_identity or {},
    )
    study_id = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    normalized_draft = _draft_from_normalized(payload["draft"])
    normalized_code = _code_from_normalized(payload["code"])
    normalized_runtime = _runtime_from_normalized(payload["runtime"])
    spec = StudySpec(
        format=STUDY_SPEC_FORMAT,
        study_id=study_id,
        draft=normalized_draft,
        code=normalized_code,
        runtime=normalized_runtime,
        model_identity=payload["model_identity"],
        config_identity=payload["config_identity"],
        artifact_root=str(derive_artifact_root(research_root, study_id).resolve()),
    )
    assert recompute_study_id(spec) == spec.study_id
    return spec


def recompute_study_id(spec: StudySpec) -> str:
    return compute_study_id(
        spec.draft,
        spec.code,
        spec.runtime,
        spec.model_identity,
        spec.config_identity,
    )


def study_spec_to_dict(spec: StudySpec) -> dict[str, object]:
    normalized = _normalize(asdict(spec), "study_spec")
    assert isinstance(normalized, dict)
    return normalized


def study_spec_from_dict(data: Mapping[str, Any]) -> StudySpec:
    normalized = _normalize(data, "study_spec")
    if not isinstance(normalized, dict):
        raise StudySpecError("study spec must be a mapping")
    try:
        spec = StudySpec(
            format=normalized["format"],
            study_id=normalized["study_id"],
            draft=_draft_from_normalized(normalized["draft"]),
            code=_code_from_normalized(normalized["code"]),
            runtime=_runtime_from_normalized(normalized["runtime"]),
            model_identity=normalized["model_identity"],
            config_identity=normalized["config_identity"],
            artifact_root=normalized["artifact_root"],
        )
    except KeyError as exc:
        raise StudySpecError(f"missing study spec field: {exc.args[0]}") from exc
    if spec.format != STUDY_SPEC_FORMAT:
        raise StudySpecError(f"unexpected study spec format: {spec.format}")
    if recompute_study_id(spec) != spec.study_id:
        raise StudySpecError("study_id does not match the study identity payload")
    return spec


def write_study_spec(spec: StudySpec, *, research_root: str | Path) -> dict[str, object]:
    if spec.format != STUDY_SPEC_FORMAT or recompute_study_id(spec) != spec.study_id:
        raise StudySpecError("cannot write an invalid study spec")
    artifact_root = derive_artifact_root(research_root, spec.study_id).resolve()
    if str(artifact_root) != spec.artifact_root:
        raise StudySpecError("research_root does not match the completed artifact_root")
    return write_immutable_json(artifact_root / "study-spec.json", study_spec_to_dict(spec))


def load_study_spec(path: str | Path) -> StudySpec:
    value = read_immutable_json(path)
    if not isinstance(value, Mapping):
        raise StudySpecError("study spec JSON must contain an object")
    return study_spec_from_dict(value)
