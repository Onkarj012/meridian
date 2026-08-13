"""Tests for deterministic research study identities."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import subprocess

import pytest

from contracts.study_spec import (
    CodeIdentity,
    RuntimeIdentity,
    StudySpecDraft,
    StudySpecError,
    build_code_identity,
    complete_study_spec,
    compute_study_id,
    recompute_study_id,
    study_spec_from_dict,
    study_spec_to_dict,
    write_study_spec,
)


def _draft() -> StudySpecDraft:
    return StudySpecDraft(
        study_type="governance.bootstrap.v1",
        research_question="Does the candidate improve net returns?",
        null_hypothesis="The candidate does not improve net returns.",
        source_snapshot_id="snapshot-1",
        source_manifest_sha256="1" * 64,
        universe={"symbols": ["AAA", "BBB"]},
        periods={"train": ["2024"], "validation": ["2025"], "lockbox": ["2026"]},
        labels={"horizon": 5},
        models={"candidate": "linear"},
        controls={"baseline": "flat"},
        cost_scenarios={"base_bps": 8},
        statistical_family="bootstrap",
        multiplicity_method="holm",
        primary_metric="net_return",
        secondary_metrics=("drawdown", "turnover"),
        pass_gate={"minimum": 0.0},
        stop_rule="Stop after the registered sample.",
        sealed=False,
        notes="fixture",
    )


def _code() -> CodeIdentity:
    return CodeIdentity("abc123", False, "2" * 64, 4, ())


def _runtime() -> RuntimeIdentity:
    return RuntimeIdentity("3.11.9", "test-platform", (("uv.lock", "3" * 64),), (("numpy", "absent"),))


def _study_id(draft: StudySpecDraft, *, model=None, config=None) -> str:
    return compute_study_id(draft, _code(), _runtime(), model or {}, config or {})


def test_identical_drafts_produce_identical_study_ids() -> None:
    assert _study_id(_draft()) == _study_id(_draft())


def test_changing_each_draft_field_changes_the_study_id() -> None:
    baseline = _study_id(_draft())
    changes = (
        ("research_question", "A different question"),
        ("primary_metric", "sharpe"),
        ("secondary_metrics", ("drawdown",)),
        ("sealed", True),
    )
    for field, value in changes:
        assert _study_id(replace(_draft(), **{field: value})) != baseline


def test_model_and_config_identity_changes_change_the_study_id() -> None:
    baseline = _study_id(_draft(), model={"name": "a"}, config={"threshold": 1})
    assert _study_id(_draft(), model={"name": "b"}, config={"threshold": 1}) != baseline
    assert _study_id(_draft(), model={"name": "a"}, config={"threshold": 2}) != baseline


def test_research_root_does_not_change_identity_and_artifact_root_ends_with_id(tmp_path) -> None:
    first = complete_study_spec(
        _draft(), repo_root=tmp_path, research_root=tmp_path / "one", code=_code(), runtime=_runtime()
    )
    second = complete_study_spec(
        _draft(), repo_root=tmp_path, research_root=tmp_path / "two", code=_code(), runtime=_runtime()
    )

    assert first.study_id == second.study_id
    assert Path(first.artifact_root).name == first.study_id
    assert Path(second.artifact_root).name == second.study_id


def test_recompute_study_id_matches_completed_spec(tmp_path) -> None:
    spec = complete_study_spec(
        _draft(), repo_root=tmp_path, research_root=tmp_path / "research", code=_code(), runtime=_runtime()
    )

    assert recompute_study_id(spec) == spec.study_id


def test_study_spec_dictionary_round_trip_is_exact(tmp_path) -> None:
    spec = complete_study_spec(
        _draft(), repo_root=tmp_path, research_root=tmp_path / "research",
        model_identity={"name": "model-a"}, config_identity={"seed": 7},
        code=_code(), runtime=_runtime(),
    )

    assert study_spec_from_dict(study_spec_to_dict(spec)) == spec


def test_identity_rejects_paths_and_non_string_mapping_keys() -> None:
    with pytest.raises(StudySpecError, match="Path"):
        _study_id(replace(_draft(), universe={"nested": {"path": Path("relative")}}))
    with pytest.raises(StudySpecError, match="non-string"):
        _study_id(replace(_draft(), universe={1: "AAA"}))


def test_code_identity_tracks_unignored_worktree_content(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-q")
    (repo / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    tracked = repo / "tracked.txt"
    tracked.write_text("one\n", encoding="utf-8")
    git("add", ".gitignore", "tracked.txt")
    git("-c", "user.email=t@e", "-c", "user.name=t", "commit", "-qm", "initial")
    baseline = build_code_identity(repo)

    untracked = repo / "untracked.txt"
    untracked.write_text("new\n", encoding="utf-8")
    with_untracked = build_code_identity(repo)
    assert with_untracked.manifest_sha256 != baseline.manifest_sha256

    (repo / "ignored.txt").write_text("ignored\n", encoding="utf-8")
    with_ignored = build_code_identity(repo)
    assert with_ignored.manifest_sha256 == with_untracked.manifest_sha256

    tracked.write_text("two\n", encoding="utf-8")
    with_edit = build_code_identity(repo)
    assert with_edit.manifest_sha256 != with_ignored.manifest_sha256
    assert with_edit.dirty is True


def test_write_study_spec_is_immutable_and_idempotent(tmp_path) -> None:
    research_root = tmp_path / "research"
    spec = complete_study_spec(
        _draft(), repo_root=tmp_path, research_root=research_root, code=_code(), runtime=_runtime()
    )

    first = write_study_spec(spec, research_root=research_root)
    second = write_study_spec(spec, research_root=research_root)
    files = list(Path(spec.artifact_root).iterdir())

    assert first["created"] is True
    assert second["created"] is False
    assert [path.name for path in files] == ["study-spec.json"]
