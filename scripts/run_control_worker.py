#!/usr/bin/env python3
"""Run the durable MERIDIAN control-job worker."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ops.control_jobs import control_root, research_root
from ops.control_runner import run_once, worker_loop


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control-root")
    parser.add_argument("--research-root")
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--iterations", type=int)
    args = parser.parse_args(argv)
    control = control_root(args.control_root)
    research = research_root(args.research_root)
    if args.once:
        for status in run_once(root=control, research=research):
            print(f"{status.job_id} {status.state} attempt={status.attempt}")
    else:
        worker_loop(
            root=control,
            research=research,
            interval_seconds=args.interval,
            iterations=args.iterations,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
