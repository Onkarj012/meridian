#!/usr/bin/env python3
"""Run the registered Sleeve F C1 walk-forward with a holdout firewall."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import pickle
import re
import subprocess
import sys
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evidence.c1_folds import expanding_quarterly_folds, inner_validation_split, split_fold
from evidence.c1_gates import evaluate_6a_wf
from evidence.c1_metrics import compute_metrics, paired_metrics, stitch_oos_daily
from evidence.c1_replay import ReplayConfig, replay
from evidence.walkforward import WalkForwardFold
from models.c1_baselines import activity_matched_baselines
from models.c1_candidates import b_scores, c_scores, predict_scores, train_candidate
from policy.c1_threshold import fit_threshold


HOLDOUT_CUTOFF = pd.Timestamp("2025-06-30 23:59:59")
HOLDOUT_START = pd.Timestamp("2025-07-01")
HOLDOUT_END = pd.Timestamp("2026-06-30 23:59:59")
DEFAULT_MATRIX_DIR = Path(os.environ.get("SLEEVE_F_C1_MATRIX_DIR", "runs/sleeve-f-c1-matrix"))
DEFAULT_WF_DIR = Path(os.environ.get("SLEEVE_F_C1_WF_DIR", "runs/sleeve-f-c1-wf"))
DEFAULT_B_ARTIFACT = os.environ.get("SLEEVE_F_C1_B_ARTIFACT")
DEFAULT_RANDOM_REPLICATES = int(os.environ.get("SLEEVE_F_C1_RANDOM_REPLICATES", "1000"))


class PreRunVerificationError(RuntimeError):
    """A registered pre-run rail prevented an outcome run."""


def _relative(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def _git_registration_files(root: Path) -> list[Path]:
    files = sorted((root / "registrations/sleeve-f/c1").glob("*.md"))
    if not files:
        raise PreRunVerificationError("PRE-RUN ABORT: no registration files found under registrations/sleeve-f/c1/*.md")
    return files


def _assert_tracked_and_clean(paths: Sequence[Path], *, root: Path, require_committed: bool = False) -> None:
    relative = [_relative(path, root) for path in paths]
    for path, rel in zip(paths, relative):
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", rel],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if tracked.returncode != 0:
            raise PreRunVerificationError(f"PRE-RUN ABORT: registration file is not git-tracked: {rel}")
        if not path.exists():
            raise PreRunVerificationError(f"PRE-RUN ABORT: tracked registration file is missing: {rel}")
    status = subprocess.run(
        ["git", "status", "--porcelain", "--", *relative],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if status.returncode != 0:
        raise PreRunVerificationError(f"PRE-RUN ABORT: unable to inspect registration status: {status.stderr.strip()}")
    if status.stdout.strip():
        raise PreRunVerificationError(
            "PRE-RUN ABORT: registration files are not clean: " + ", ".join(relative)
        )
    if require_committed:
        committed = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", *relative],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        if committed.returncode != 0:
            raise PreRunVerificationError("PRE-RUN ABORT: registration files are not committed")


def registration_sleeve_capital(registration_path: Path) -> tuple[float | None, bool]:
    text = registration_path.read_text(encoding="utf-8")
    match = re.search(r"^\*\*Sleeve capital:\s*(.+?)\*\*", text, flags=re.MULTILINE)
    if match is None:
        match = re.search(r"^[-*]\s*\*\*Sleeve capital:\s*(.+?)\*\*", text, flags=re.MULTILINE)
    line = match.group(1) if match else ""
    placeholder = "PLACEHOLDER" in line.upper()
    if placeholder:
        return None, True
    number = re.search(r"[-+]?\d+(?:\.\d+)?", line.replace(",", ""))
    return (float(number.group(0)) if number else None), False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _matrix_hash(hashes_path: Path) -> str:
    try:
        payload = json.loads(hashes_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreRunVerificationError(f"PRE-RUN ABORT: invalid matrix hashes.json: {hashes_path}") from exc
    value = payload.get("c1", {}).get("sha256") if isinstance(payload.get("c1"), Mapping) else None
    if value is None:
        value = payload.get("sha256")
    if not value:
        raise PreRunVerificationError(f"PRE-RUN ABORT: hashes.json has no C1 matrix sha256: {hashes_path}")
    return str(value)


def load_matrix(matrix_dir: Path) -> tuple[pd.DataFrame, str]:
    matrix_path = matrix_dir / "c1.parquet"
    hashes_path = matrix_dir / "hashes.json"
    if not matrix_path.exists():
        raise PreRunVerificationError(f"PRE-RUN ABORT: matrix artifact does not exist: {matrix_path}")
    if not hashes_path.exists():
        raise PreRunVerificationError(f"PRE-RUN ABORT: matrix hashes.json does not exist: {hashes_path}")
    matrix_hash = _matrix_hash(hashes_path)
    try:
        matrix = pd.read_parquet(matrix_path)
    except Exception as exc:  # pragma: no cover - exact parquet backend errors vary
        raise PreRunVerificationError(f"PRE-RUN ABORT: unable to load C1 matrix: {matrix_path}: {exc}") from exc
    if "datetime" not in matrix:
        raise PreRunVerificationError("PRE-RUN ABORT: C1 matrix needs datetime for the holdout firewall")
    return matrix, matrix_hash


def _assert_firewall(rows: pd.DataFrame, label: str, probe: Callable[[str, pd.DataFrame], None] | None = None) -> None:
    if not isinstance(rows, pd.DataFrame):
        raise AssertionError(f"holdout firewall received non-DataFrame at {label}")
    if "datetime" not in rows:
        raise AssertionError(f"holdout firewall frame at {label} has no datetime")
    timestamps = pd.to_datetime(rows["datetime"], errors="raise")
    assert not (timestamps > HOLDOUT_CUTOFF).any(), (
        f"holdout firewall breached at {label}: post-2025-06-30 row entered downstream data"
    )
    if probe is not None:
        probe(label, rows)


def apply_holdout_firewall(
    matrix: pd.DataFrame,
    *,
    probe: Callable[[str, pd.DataFrame], None] | None = None,
) -> pd.DataFrame:
    """Drop post-cutoff rows once, then assert the invariant immediately."""
    timestamps = pd.to_datetime(matrix["datetime"], errors="raise")
    filtered = matrix.loc[timestamps <= HOLDOUT_CUTOFF].copy()
    _assert_firewall(filtered, "matrix-after-firewall", probe)
    return filtered.reset_index(drop=True)


def _calendar_for(
    rows: pd.DataFrame,
    explicit: pd.DataFrame | str | None,
    *,
    smoke: bool = False,
) -> pd.DataFrame | str | None:
    if explicit is not None:
        return explicit
    if "front_expiry" in rows:
        calendar = rows[["trade_date", "front_expiry"]].copy() if "trade_date" in rows else rows.assign(
            trade_date=pd.to_datetime(rows["datetime"]).dt.date
        )[["trade_date", "front_expiry"]]
        calendar["front_instrument_id"] = calendar["front_expiry"].astype(str)
        return calendar.drop_duplicates("trade_date")
    if not smoke:
        # Production replay owns the validated calendar default.  Never make
        # up expiry data for an outcome run when the matrix has no calendar.
        return None
    dates = pd.to_datetime(rows["datetime"]).dt.date.drop_duplicates().sort_values()
    return pd.DataFrame({
        "trade_date": dates,
        "front_expiry": [pd.Timestamp(day) + pd.Timedelta(days=30) for day in dates],
        "front_instrument_id": [f"SYNTH-{day}" for day in dates],
    })


def _write_frame(frame: pd.DataFrame, path: Path) -> None:
    output = frame.reset_index() if frame.index.name == "trade_date" else frame.copy()
    output.to_csv(path, index=False, date_format="%Y-%m-%dT%H:%M:%S")


def _read_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    for column in ("datetime", "trade_date", "decision_datetime", "entry_datetime", "exit_datetime", "front_expiry"):
        if column in frame:
            frame[column] = pd.to_datetime(frame[column], errors="coerce")
    if "trade_date" in frame and frame["trade_date"].notna().all():
        frame["trade_date"] = frame["trade_date"].dt.date
    return frame.set_index("trade_date") if "trade_date" in frame.columns and path.name == "daily.csv" else frame


def _unit_hash(unit_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(path for path in unit_dir.rglob("*") if path.is_file() and path.name != "COMPLETE.json"):
        digest.update(str(path.relative_to(unit_dir)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _unit_complete(unit_dir: Path) -> bool:
    marker = unit_dir / "COMPLETE.json"
    if not marker.exists():
        return False
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return payload.get("content_hash") == _unit_hash(unit_dir)


def _mark_unit_complete(unit_dir: Path) -> None:
    marker = {"content_hash": _unit_hash(unit_dir), "marker_version": 1}
    (unit_dir / "COMPLETE.json").write_text(
        json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )


def _fold_from_dict(payload: Mapping[str, Any]) -> WalkForwardFold:
    return WalkForwardFold(
        name=str(payload["name"]),
        select_start=pd.Timestamp(payload["select_start"]),
        select_end=pd.Timestamp(payload["select_end"]),
        oos_start=pd.Timestamp(payload["oos_start"]),
        oos_end=pd.Timestamp(payload["oos_end"]),
    )


def _smoke_folds(rows: pd.DataFrame) -> list[WalkForwardFold]:
    dates = sorted(pd.to_datetime(rows["datetime"]).dt.normalize().unique())
    if len(dates) < 4:
        raise ValueError("SMOKE requires at least four synthetic sessions (two folds)")
    folds: list[WalkForwardFold] = []
    for index, test_index in enumerate((2, 3), start=1):
        folds.append(WalkForwardFold(
            name=f"fold_{index}",
            select_start=pd.Timestamp(dates[0]),
            select_end=pd.Timestamp(dates[test_index - 1]),
            oos_start=pd.Timestamp(dates[test_index]),
            oos_end=pd.Timestamp(dates[test_index]),
        ))
    return folds


def _score_rows(model: Any, rows: pd.DataFrame, candidate: str, b_artifact: Mapping[str, Any] | None) -> pd.DataFrame:
    scored = rows.copy()
    if candidate == "B":
        if b_artifact is None:
            raise ValueError("candidate B requires a frozen constants artifact")
        scored["score"] = b_scores(scored, b_artifact)
    else:
        raw = predict_scores(model, scored, candidate)
        scored["score"] = c_scores(raw) if candidate == "C" else raw
    return scored


def _unit_paths(unit_dir: Path) -> dict[str, Path]:
    return {
        "model": unit_dir / "model.pkl",
        "threshold": unit_dir / "threshold.json",
        "trades": unit_dir / "trades.csv",
        "daily": unit_dir / "daily.csv",
        "baselines": unit_dir / "baseline_summaries.json",
        "random_null": unit_dir / "random_null.json",
        "metadata": unit_dir / "unit.json",
    }


def _load_unit(unit_dir: Path) -> dict[str, Any]:
    paths = _unit_paths(unit_dir)
    metadata = json.loads(paths["metadata"].read_text(encoding="utf-8"))
    return {
        "metadata": metadata,
        "trades": _read_frame(paths["trades"]),
        "daily": _read_frame(paths["daily"]),
        "baselines": json.loads(paths["baselines"].read_text(encoding="utf-8")),
    }


def _run_unit(
    *,
    fold: WalkForwardFold,
    candidate: str,
    samples: Any,
    unit_dir: Path,
    sleeve_capital: float,
    contract_calendar: pd.DataFrame | str,
    random_replicates: int,
    b_artifact: Mapping[str, Any] | None,
    smoke: bool,
    probe: Callable[[str, pd.DataFrame], None] | None,
) -> dict[str, Any]:
    unit_dir.mkdir(parents=True, exist_ok=True)
    if _unit_complete(unit_dir):
        loaded = _load_unit(unit_dir)
        loaded["skipped_resume"] = True
        return loaded

    train, test = samples.train, samples.test
    _assert_firewall(train, f"{fold.name}/{candidate}/train", probe)
    _assert_firewall(test, f"{fold.name}/{candidate}/test", probe)
    paths = _unit_paths(unit_dir)
    inner_train, validation = inner_validation_split(train)
    _assert_firewall(inner_train, f"{fold.name}/{candidate}/inner-train", probe)
    _assert_firewall(validation, f"{fold.name}/{candidate}/inner-validation", probe)

    if candidate == "B":
        scored_validation = _score_rows(None, validation, candidate, b_artifact)
        threshold = 0.5
        threshold_payload = {"threshold": threshold, "diagnostics": {"rule": "frozen candidate B constants"}}
        model = None
    else:
        # Models fit on the inner 80% only; the validation slice is the
        # early-stop / threshold-fitting set and must stay out of training.
        trained = train_candidate(candidate, inner_train, validation, fold=fold.name)
        model = trained.model
        scored_validation = _score_rows(model, validation, candidate, None)
        fitted = fit_threshold(
            scored_validation,
            sleeve_capital=sleeve_capital,
            contract_calendar=contract_calendar,
        )
        threshold = float(fitted.threshold)
        threshold_payload = {"threshold": threshold, "diagnostics": fitted.diagnostics}
        paths["model"].write_bytes(pickle.dumps(model, protocol=5))

    scored_test = _score_rows(model, test, candidate, b_artifact)
    _assert_firewall(scored_test, f"{fold.name}/{candidate}/scored-test", probe)
    replayed = replay(
        scored_test,
        threshold,
        sleeve_capital=sleeve_capital,
        contract_calendar=contract_calendar,
        config=ReplayConfig(),
    )
    baselines = activity_matched_baselines(
        scored_test,
        replayed,
        candidate=candidate,
        fold=fold.name,
        sleeve_capital=sleeve_capital,
        contract_calendar=contract_calendar,
        random_replicates=1 if smoke else random_replicates,
    )

    paths["threshold"].write_text(json.dumps(threshold_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_frame(replayed.trades, paths["trades"])
    _write_frame(replayed.daily, paths["daily"])
    baseline_payload: dict[str, Any] = {}
    random_null: list[dict[str, Any]] = []
    for name, result in sorted(baselines.items()):
        daily_name = f"baseline_{name}_daily.csv"
        trades_name = f"baseline_{name}_trades.csv"
        _write_frame(result.daily, unit_dir / daily_name)
        _write_frame(result.trades, unit_dir / trades_name)
        baseline_payload[name] = {
            "metadata": result.metadata,
            "target_trade_count": result.target_trade_count,
            "executed_trade_count": result.executed_trade_count,
            "daily_file": daily_name,
            "trades_file": trades_name,
            "null_count": len(result.null_distribution),
        }
        if name == "random_entry":
            random_null = list(result.null_distribution)
    paths["baselines"].write_text(json.dumps(baseline_payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    paths["random_null"].write_text(json.dumps(random_null, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    metadata = {
        "candidate": candidate,
        "fold": fold.as_dict(),
        "oos_start": fold.oos_start.date().isoformat(),
        "oos_end": fold.oos_end.date().isoformat(),
        "threshold": threshold,
        "model_metadata": trained.metadata if candidate != "B" else {"candidate": "B", "artifact": "frozen-constants"},
        "smoke": bool(smoke),
    }
    paths["metadata"].write_text(json.dumps(metadata, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    _mark_unit_complete(unit_dir)
    return _load_unit(unit_dir)


def _baseline_fold_artifacts(units: Mapping[str, Any], baseline_name: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for fold, unit in units.items():
        descriptor = unit["baselines"].get(baseline_name)
        if descriptor is None:
            continue
        directory = unit["unit_dir"]
        result[fold] = {
            "daily": _read_frame(directory / descriptor["daily_file"]),
            "trades": _read_frame(directory / descriptor["trades_file"]),
            "oos_start": unit["metadata"]["oos_start"],
            "oos_end": unit["metadata"]["oos_end"],
        }
    return result


def _candidate_report(units: Mapping[str, Any], *, smoke: bool) -> dict[str, Any]:
    fold_artifacts = {
        fold: {
            "daily": unit["daily"],
            "trades": unit["trades"],
            "oos_start": unit["metadata"]["oos_start"],
            "oos_end": unit["metadata"]["oos_end"],
        }
        for fold, unit in units.items()
    }
    if smoke:
        return {
            "status": "SMOKE",
            "metrics": "SUPPRESSED",
            "folds": sorted(units),
            "trade_count": int(sum(len(unit["trades"]) for unit in units.values())),
        }
    all_trades = pd.concat([unit["trades"] for unit in units.values()], ignore_index=True) if units else pd.DataFrame()
    metrics = compute_metrics(fold_artifacts, trades=all_trades, dsr_k=95)
    paired: dict[str, Any] = {}
    for baseline in ("time_of_day", "volatility", "unconditional_long", "random_entry"):
        baseline_artifacts = _baseline_fold_artifacts(units, baseline)
        if baseline_artifacts:
            paired[baseline] = paired_metrics(fold_artifacts, baseline_artifacts)
    preview_deltas = [value["delta_sharpe"] for value in paired.values()]
    preview_lows = [value["paired_mbb_ci_low"] for value in paired.values()]
    preview = {
        "gate": "6b_wf_preview",
        "status": "PREVIEW_ONLY_HOLDOUT_REQUIRED",
        "passed": bool(preview_deltas) and min(preview_deltas) >= 0.25 and min(preview_lows) > 0,
        "legs": {
            "wf_delta_sharpe": {"value": min(preview_deltas) if preview_deltas else None, "threshold": 0.25, "operator": ">="},
            "wf_paired_mbb_ci_low": {"value": min(preview_lows) if preview_lows else None, "threshold": 0.0, "operator": ">"},
        },
        "aggregation": "conservative minimum across all matched baselines",
    }
    beats_baselines = bool(paired) and all(
        float(value.get("mean_daily_pnl_diff", 0.0)) > 0.0 for value in paired.values()
    )
    wf_gate_input = {
        "wf": {
            **metrics,
            "trades": all_trades,
            "daily": stitch_oos_daily(fold_artifacts),
            "operationally_executable": True,
            "beats_matched_baselines": beats_baselines,
        }
    }
    return {
        "status": "OUTCOME_WF",
        "metrics": metrics,
        "paired_vs_baselines": paired,
        "six_a_wf": evaluate_6a_wf(wf_gate_input),
        "six_b_wf_preview": preview,
        "dsr_k": 95,
        "folds": sorted(units),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix-dir", type=Path, default=DEFAULT_MATRIX_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_WF_DIR)
    parser.add_argument("--b-artifact", type=Path, default=Path(DEFAULT_B_ARTIFACT) if DEFAULT_B_ARTIFACT else None)
    parser.add_argument("--contract-calendar", type=Path)
    parser.add_argument("--sleeve-capital", type=float)
    parser.add_argument("--random-replicates", type=int, default=DEFAULT_RANDOM_REPLICATES)
    parser.add_argument("--smoke", action="store_true")
    return parser


def run(
    args: argparse.Namespace,
    *,
    root: Path = REPO_ROOT,
    matrix: pd.DataFrame | None = None,
    folds: Iterable[WalkForwardFold] | None = None,
    firewall_probe: Callable[[str, pd.DataFrame], None] | None = None,
) -> dict[str, Any]:
    registrations = _git_registration_files(root)
    _assert_tracked_and_clean(registrations, root=root)
    registered_capital, placeholder = registration_sleeve_capital(root / "registrations/sleeve-f/c1/registration.md")
    if placeholder and not args.smoke:
        raise PreRunVerificationError("PRE-RUN ABORT: registration sleeve capital is PLACEHOLDER; freeze §5 before an outcome run")
    if args.sleeve_capital is not None and not args.smoke:
        raise PreRunVerificationError("PRE-RUN ABORT: --sleeve-capital override is allowed only for --smoke")
    if args.sleeve_capital is not None:
        sleeve_capital = float(args.sleeve_capital)
        capital_source = "UNREGISTERED-OVERRIDE"
    elif registered_capital is not None:
        sleeve_capital = registered_capital
        capital_source = "REGISTRATION"
    else:
        raise PreRunVerificationError("PRE-RUN ABORT: sleeve capital is not numeric in registration.md")
    if sleeve_capital <= 0:
        raise PreRunVerificationError("PRE-RUN ABORT: sleeve capital must be positive")
    if matrix is None:
        matrix, matrix_hash = load_matrix(root / args.matrix_dir if not args.matrix_dir.is_absolute() else args.matrix_dir)
    else:
        matrix_hash = "INJECTED-MATRIX"
    matrix = apply_holdout_firewall(matrix, probe=firewall_probe)
    if matrix.empty:
        raise ValueError("C1 matrix has no pre-holdout rows after firewall")
    chosen_folds = list(folds) if folds is not None else (_smoke_folds(matrix) if args.smoke else expanding_quarterly_folds())
    calendar_source = args.contract_calendar
    contract_calendar = _calendar_for(matrix, calendar_source, smoke=args.smoke)
    if isinstance(contract_calendar, Path):
        contract_calendar = str(contract_calendar)
    b_artifact = None
    if args.b_artifact is not None and args.b_artifact.exists():
        b_artifact = json.loads(args.b_artifact.read_text(encoding="utf-8"))
    out_dir = root / args.out_dir if not args.out_dir.is_absolute() else args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    unit_results: dict[str, dict[str, dict[str, Any]]] = {candidate: {} for candidate in ("A", "B", "C")}
    skipped: dict[str, str] = {}
    for fold in chosen_folds:
        samples = split_fold(matrix, fold)
        _assert_firewall(samples.train, f"{fold.name}/train", firewall_probe)
        _assert_firewall(samples.test, f"{fold.name}/test", firewall_probe)
        for candidate in ("A", "B", "C"):
            if candidate == "B" and b_artifact is None:
                skipped[fold.name] = "SKIP candidate B: no --b-artifact supplied; registered minute windows remain an open governance item"
                continue
            unit_dir = out_dir / fold.name / candidate
            unit = _run_unit(
                fold=fold, candidate=candidate, samples=samples, unit_dir=unit_dir,
                sleeve_capital=sleeve_capital, contract_calendar=contract_calendar,
                random_replicates=args.random_replicates, b_artifact=b_artifact,
                smoke=args.smoke, probe=firewall_probe,
            )
            unit["unit_dir"] = unit_dir
            unit_results[candidate][fold.name] = unit

    candidate_reports = {
        candidate: _candidate_report(units, smoke=args.smoke) if units else {
            "status": "SKIPPED",
            "reason": "no --b-artifact supplied; registered minute windows remain an open governance item",
            "folds": [],
        }
        for candidate, units in unit_results.items()
    }
    # No surviving candidate is a 6c kill signal, not a default to A; the
    # holdout runner refuses a null selection.
    selected = next((candidate for candidate, report in candidate_reports.items() if report.get("status") != "SMOKE" and report.get("six_a_wf", {}).get("passed") and report.get("six_b_wf_preview", {}).get("passed")), None)
    summary: dict[str, Any] = {
        "run_type": "SMOKE" if args.smoke else "OUTCOME_WF",
        "status": "SMOKE — NOT AN OUTCOME RUN" if args.smoke else "OUTCOME_WF",
        "capital": {"value": sleeve_capital, "source": capital_source},
        "matrix": {"sha256": matrix_hash, "rows_after_firewall": len(matrix), "holdout_cutoff": str(HOLDOUT_CUTOFF)},
        "firewall": {"rule": "drop and assert datetime <= 2025-06-30 23:59:59 after load and at every fold/downstream frame"},
        "folds": [fold.as_dict() for fold in chosen_folds],
        "candidates": candidate_reports,
        "candidate_B": {"status": "SKIPPED", "reason": next(iter(skipped.values()))} if skipped else {"status": "RUN"},
        "selected_candidate": selected,
        "final_configuration": {"candidate": selected, "fold": chosen_folds[-1].name if chosen_folds else None},
        "dsr_k": 95,
        "deterministic": True,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    lines = [
        "# Sleeve F C1 walk-forward",
        "",
        f"**Status:** {summary['status']}",
        f"**Matrix SHA-256:** `{matrix_hash}`",
        f"**Sleeve capital:** `{sleeve_capital}` ({capital_source})",
        "",
        "## Pre-run and firewall",
        "",
        "Registration files were required to be tracked and clean. Rows after 2025-06-30 23:59:59 were dropped immediately after load and asserted absent after every fold split and downstream scored frame.",
        "",
        "## Candidates",
        "",
    ]
    for candidate, report in candidate_reports.items():
        lines.append(f"### Candidate {candidate}")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps(report, indent=2, sort_keys=True, default=str))
        lines.append("```")
        lines.append("")
    if skipped:
        lines.extend(["Candidate B: **SKIPPED** — no frozen B constants artifact supplied; registered minute windows remain an open governance item.", ""])
    lines.append("DSR trial count: **k = 95**. 6b is a WF preview only; full 6b requires the separate one-shot holdout.")
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = run(args)
    except (PreRunVerificationError, AssertionError, ValueError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"status": summary["status"], "out_dir": str(args.out_dir)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
