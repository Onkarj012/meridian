#!/usr/bin/env python3
"""Assess and optionally run the bounded historical acquisition checkpoint.

The CLI reads only an immutable session-ledger report and, in ``--execute``
mode, the ledger's ``sessions.jsonl``.  Readiness mode performs no network
access and inspects no strategy outcome.  ``--execute`` fails closed on an
unqualified denominator: it returns a ``NOT_RUN`` decision without contacting
any source.  A qualified ledger would run the resumable, per-URL-bounded
checkpoint through the existing daily collector.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from contracts.calendar import canonical_json
from evidence.daily_acquisition_checkpoint import (
    CheckpointError,
    assess_checkpoint,
    run_bounded_checkpoint,
)
from ingest.calendar import publish_directory

DEFAULT_EXISTING_CASH_ROOT = Path("derived_data/daily-graph/cash-evidence-v1")
DEFAULT_SHARED_BUDGET = Path("derived_data/daily-graph/acquisition-budget-v1/reservations.jsonl")
# The durable audit summary is imported by default when present, so a fresh
# output/route directory cannot forget the known prior consumption.  Its absence
# is allowed only for a genuinely fresh workspace; a present-but-malformed file
# fails closed inside the runner's seed aggregation.
DEFAULT_SEED_BUDGET = Path(".scratch/daily-graph-system/network-budget-state.json")


def _read_json(path: Path) -> Any:
    if not path.is_file():
        raise SystemExit(f"input json does not exist: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"input json is not valid JSON: {path}") from exc


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise SystemExit(f"sessions file does not exist: {path}")
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        decoded = json.loads(line)
        if not isinstance(decoded, dict):
            raise SystemExit("each sessions row must be a JSON object")
        rows.append(decoded)
    return rows


def _expected_session_dates(rows: list[dict[str, Any]]) -> list[str]:
    dates: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if row.get("state") != "expected":
            continue
        session_date = row.get("session_date")
        if not isinstance(session_date, str):
            raise SystemExit("an expected session row is missing its session_date")
        if session_date in seen:
            raise SystemExit(f"duplicate expected session date in sessions file: {session_date}")
        seen.add(session_date)
        dates.append(session_date)
    return dates


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger-report", type=Path, required=True, help="immutable session-ledger report.json")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--sessions", type=Path, default=None, help="ledger sessions.jsonl (required to execute)")
    parser.add_argument("--route", default="nsearchives_full_bhavdata", help="allowlisted daily cash route")
    parser.add_argument(
        "--seed-budget",
        type=Path,
        default=DEFAULT_SEED_BUDGET,
        help=(
            "durable per-URL budget summary to import by default when present; the audit "
            "summary is used unless a different path is supplied"
        ),
    )
    parser.add_argument(
        "--shared-budget",
        type=Path,
        default=DEFAULT_SHARED_BUDGET,
        help="durable, file-locked shared per-URL reservation journal (independent of the run output root)",
    )
    parser.add_argument(
        "--existing-cash-root",
        type=Path,
        action="append",
        default=None,
        help="retained cash evidence root to reuse (repeatable); defaults to cash-evidence-v1",
    )
    parser.add_argument("--execute", action="store_true", help="attempt acquisition; refuses an unqualified denominator")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = _read_json(args.ledger_report)
    if not isinstance(report, dict):
        raise SystemExit("ledger report must be a JSON object")

    if not args.execute:
        try:
            decision = assess_checkpoint(report)
        except CheckpointError as exc:
            raise SystemExit(f"checkpoint readiness could not be assessed: {exc}") from exc
        if args.output_root.exists() or args.output_root.is_symlink():
            raise SystemExit(f"refusing existing immutable output root: {args.output_root}")
        publish_directory(args.output_root, {"decision.json": canonical_json(decision)})
        print(json.dumps({
            "output_root": str(args.output_root),
            "status": decision["status"],
            "denominator_qualified": decision["denominator_qualified"],
            "blocker_count": decision["blocker_count"],
            "reason": decision["reason"],
        }, sort_keys=True, indent=2))
        return 0

    if args.sessions is None:
        raise SystemExit("--execute requires --sessions")

    from ingest.daily_cash import single_http_transport

    rows = _read_rows(args.sessions)
    expected_dates = _expected_session_dates(rows)
    existing_roots = tuple(args.existing_cash_root or [DEFAULT_EXISTING_CASH_ROOT])
    try:
        run = run_bounded_checkpoint(
            report,
            output_root=args.output_root,
            transport=single_http_transport,
            route=args.route,
            expected_session_dates=expected_dates,
            existing_cash_roots=existing_roots,
            shared_budget_path=args.shared_budget,
            seed_state_path=args.seed_budget,
        )
    except CheckpointError as exc:
        raise SystemExit(f"checkpoint run could not be started: {exc}") from exc

    print(json.dumps({
        "output_root": str(args.output_root),
        "status": run["status"],
        "denominator_qualified": run["denominator_qualified"],
        "expected_session_count": run.get("expected_session_count"),
        "recovered_session_denominator": run.get("recovered_session_denominator"),
        "reason": run["reason"],
    }, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
