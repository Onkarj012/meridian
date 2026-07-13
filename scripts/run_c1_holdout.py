#!/usr/bin/env python3
"""Open and run the one-shot Sleeve F C1 holdout."""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import pickle
import sys
from typing import Any, Mapping, Sequence

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from evidence.c1_gates import evaluate_6a_holdout, evaluate_6b
from evidence.c1_metrics import compute_metrics, paired_metrics
from evidence.c1_replay import replay
from models.c1_baselines import activity_matched_baselines
from models.c1_candidates import b_scores, c_scores, predict_scores
from scripts.run_c1_walkforward import (
    DEFAULT_B_ARTIFACT,
    DEFAULT_MATRIX_DIR,
    DEFAULT_RANDOM_REPLICATES,
    HOLDOUT_END,
    HOLDOUT_START,
    PreRunVerificationError,
    _assert_firewall,
    _assert_tracked_and_clean,
    _calendar_for,
    _git_registration_files,
    _read_frame,
    _relative,
    load_matrix,
    registration_sleeve_capital,
)


DEFAULT_HOLDOUT_DIR = Path(os.environ.get("SLEEVE_F_C1_HOLDOUT_DIR", "runs/sleeve-f-c1-holdout"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix-dir", type=Path, default=DEFAULT_MATRIX_DIR)
    parser.add_argument("--wf-dir", type=Path, default=Path("runs/sleeve-f-c1-wf"))
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_HOLDOUT_DIR)
    parser.add_argument("--b-artifact", type=Path, default=Path(DEFAULT_B_ARTIFACT) if DEFAULT_B_ARTIFACT else None)
    parser.add_argument("--contract-calendar", type=Path)
    parser.add_argument("--random-replicates", type=int, default=DEFAULT_RANDOM_REPLICATES)
    parser.add_argument("--candidate", choices=("A", "B", "C"))
    parser.add_argument("--open-holdout", action="store_true")
    parser.add_argument("--yes-i-understand-one-shot", action="store_true")
    return parser


def _require_summary(path: Path, *, root: Path) -> dict[str, Any]:
    if not path.exists():
        raise PreRunVerificationError(f"HOLDOUT ABORT: WF summary does not exist: {path}")
    _assert_tracked_and_clean([path], root=root, require_committed=True)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PreRunVerificationError(f"HOLDOUT ABORT: WF summary is not valid JSON: {path}") from exc


def _confirm_one_shot(yes_i_understand: bool) -> None:
    print("WARNING: HOLDOUT §7 IS ONE-SHOT. Once opened, it is spent and cannot be rerun.", file=sys.stderr)
    print("WARNING: this run consumes the frozen 2025-07-01..2026-06-30 holdout.", file=sys.stderr)
    if yes_i_understand:
        return
    confirmation = input("Type OPEN-HOLDOUT to proceed: ")
    if confirmation != "OPEN-HOLDOUT":
        raise PreRunVerificationError("HOLDOUT ABORT: confirmation did not equal OPEN-HOLDOUT")


def _load_frozen_decision(wf_dir: Path, candidate: str, fold: str) -> tuple[Any, float, dict[str, Any]]:
    unit_dir = wf_dir / fold / candidate
    marker = unit_dir / "COMPLETE.json"
    metadata_path = unit_dir / "unit.json"
    threshold_path = unit_dir / "threshold.json"
    model_path = unit_dir / "model.pkl"
    if not marker.exists() or not metadata_path.exists() or not threshold_path.exists():
        raise PreRunVerificationError(f"HOLDOUT ABORT: frozen WF artifact is incomplete: {unit_dir}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    threshold = float(json.loads(threshold_path.read_text(encoding="utf-8"))["threshold"])
    if candidate == "B":
        model = None
    else:
        if not model_path.exists():
            raise PreRunVerificationError(f"HOLDOUT ABORT: frozen model is missing: {model_path}")
        model = pickle.loads(model_path.read_bytes())
    return model, threshold, metadata


def _score(model: Any, rows: pd.DataFrame, candidate: str, b_artifact: Mapping[str, Any] | None) -> pd.DataFrame:
    scored = rows.copy()
    if candidate == "B":
        if b_artifact is None:
            raise PreRunVerificationError("HOLDOUT ABORT: candidate B needs --b-artifact")
        scored["score"] = b_scores(scored, b_artifact)
    else:
        raw = predict_scores(model, scored, candidate)
        scored["score"] = c_scores(raw) if candidate == "C" else raw
    return scored


def run(args: argparse.Namespace, *, root: Path = REPO_ROOT) -> dict[str, Any]:
    registrations = _git_registration_files(root)
    _assert_tracked_and_clean(registrations, root=root)
    summary_path = root / args.wf_dir if not args.wf_dir.is_absolute() else args.wf_dir
    summary_path = summary_path / "summary.json"
    wf_summary = _require_summary(summary_path, root=root)
    if not args.open_holdout:
        raise PreRunVerificationError("HOLDOUT ABORT: explicit --open-holdout is required")
    registered_capital, placeholder = registration_sleeve_capital(root / "registrations/sleeve-f/c1/registration.md")
    if placeholder:
        raise PreRunVerificationError("HOLDOUT ABORT: registration sleeve capital is PLACEHOLDER; freeze §5 before opening holdout")
    if registered_capital is None or registered_capital <= 0:
        raise PreRunVerificationError("HOLDOUT ABORT: registration sleeve capital is missing or non-positive")
    _confirm_one_shot(args.yes_i_understand_one_shot)

    matrix_dir = root / args.matrix_dir if not args.matrix_dir.is_absolute() else args.matrix_dir
    matrix, matrix_hash = load_matrix(matrix_dir)
    timestamps = pd.to_datetime(matrix["datetime"], errors="raise")
    holdout = matrix.loc[(timestamps >= HOLDOUT_START) & (timestamps <= HOLDOUT_END)].copy().reset_index(drop=True)
    if holdout.empty:
        raise PreRunVerificationError("HOLDOUT ABORT: matrix has no rows in 2025-07-01..2026-06-30")
    holdout_timestamps = pd.to_datetime(holdout["datetime"], errors="raise")
    assert not (holdout_timestamps < HOLDOUT_START).any(), "holdout range breach: pre-holdout row entered holdout replay"
    assert not (holdout_timestamps > HOLDOUT_END).any(), "holdout range breach: post-2026-06-30 row entered holdout replay"

    candidate = args.candidate or wf_summary.get("selected_candidate")
    final = wf_summary.get("final_configuration", {})
    candidate = candidate or final.get("candidate")
    if not candidate:
        raise PreRunVerificationError(
            "HOLDOUT ABORT: WF summary has no surviving selected candidate (6c kill); "
            "holdout stays closed unless --candidate is passed explicitly"
        )
    candidate = str(candidate).upper()
    fold = str(final.get("fold") or wf_summary.get("folds", [{}])[-1].get("name", "fold_18"))
    wf_dir = root / args.wf_dir if not args.wf_dir.is_absolute() else args.wf_dir
    model, threshold, model_metadata = _load_frozen_decision(wf_dir, candidate, fold)
    b_artifact = None
    if args.b_artifact is not None and args.b_artifact.exists():
        b_artifact = json.loads(args.b_artifact.read_text(encoding="utf-8"))
    contract_calendar = _calendar_for(holdout, args.contract_calendar)
    scored = _score(model, holdout, candidate, b_artifact)
    scored_timestamps = pd.to_datetime(scored["datetime"], errors="raise")
    assert not (scored_timestamps < HOLDOUT_START).any(), "holdout range breach: pre-holdout scored row"
    assert not (scored_timestamps > HOLDOUT_END).any(), "holdout range breach: post-2026-06-30 scored row"
    result = replay(scored, threshold, sleeve_capital=registered_capital, contract_calendar=contract_calendar)
    baseline_results = activity_matched_baselines(
        scored,
        result,
        candidate=candidate,
        fold=f"holdout-{fold}",
        sleeve_capital=registered_capital,
        contract_calendar=contract_calendar,
        random_replicates=args.random_replicates,
    )
    holdout_artifact = {"holdout": {"daily": result.daily, "trades": result.trades, "oos_start": str(HOLDOUT_START.date()), "oos_end": str(HOLDOUT_END.date())}}
    metrics = compute_metrics(holdout_artifact, trades=result.trades, dsr_k=95)
    trade_dates = pd.to_datetime(result.trades["trade_date"], errors="coerce") if not result.trades.empty else pd.Series(dtype="datetime64[ns]")
    half_counts = [int((trade_dates < pd.Timestamp("2026-01-01")).sum()), int((trade_dates >= pd.Timestamp("2026-01-01")).sum())]
    post_count = int((trade_dates >= pd.Timestamp("2026-04-01")).sum())
    six_a = evaluate_6a_holdout({"holdout": {**metrics, "trades": result.trades, "daily": result.daily, "trades_per_holdout_half": half_counts, "post_2026_04_trades": post_count}})

    paired: dict[str, Any] = {}
    baseline_artifacts = {}
    for name, baseline in sorted(baseline_results.items()):
        baseline_artifacts[name] = {"daily": baseline.daily, "trades": baseline.trades}
        paired[name] = paired_metrics({"daily": result.daily, "trades": result.trades}, {"daily": baseline.daily, "trades": baseline.trades})
    wf_paired = wf_summary.get("candidates", {}).get(candidate, {}).get("paired_vs_baselines", {})
    wf_deltas = [float(value["delta_sharpe"]) for value in wf_paired.values() if "delta_sharpe" in value]
    wf_lows = [float(value["paired_mbb_ci_low"]) for value in wf_paired.values() if "paired_mbb_ci_low" in value]
    holdout_deltas = [float(value["delta_sharpe"]) for value in paired.values()]
    holdout_lows = [float(value["paired_mbb_ci_low"]) for value in paired.values()]
    six_b = evaluate_6b({
        "wf": {"wf_delta_sharpe": min(wf_deltas) if wf_deltas else None, "wf_paired_mbb_ci_low": min(wf_lows) if wf_lows else None},
        "holdout": {"holdout_delta_sharpe": min(holdout_deltas) if holdout_deltas else None, "holdout_paired_mbb_ci_low": min(holdout_lows) if holdout_lows else None},
    })
    output = {
        "run_type": "ONE_SHOT_HOLDOUT",
        "status": "HOLDOUT_OPENED_ONCE",
        "candidate": candidate,
        "wf_fold_artifact": fold,
        "matrix_sha256": matrix_hash,
        "frozen_model_metadata": model_metadata,
        "threshold": threshold,
        "metrics": metrics,
        "paired_vs_baselines": paired,
        "six_a_holdout": six_a,
        "six_b": six_b,
        "dsr_k": 95,
        "refit": False,
    }
    out_dir = root / args.out_dir if not args.out_dir.is_absolute() else args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "trades.csv").write_text(result.trades.to_csv(index=False), encoding="utf-8")
    result.daily.reset_index().to_csv(out_dir / "daily.csv", index=False)
    (out_dir / "summary.json").write_text(json.dumps(output, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    (out_dir / "report.md").write_text(
        "# Sleeve F C1 one-shot holdout\n\n"
        "**WARNING:** §7 holdout is opened once and is now spent.\n\n"
        + "```json\n" + json.dumps(output, indent=2, sort_keys=True, default=str) + "\n```\n",
        encoding="utf-8",
    )
    return output


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except (PreRunVerificationError, AssertionError, ValueError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"status": result["status"], "candidate": result["candidate"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
