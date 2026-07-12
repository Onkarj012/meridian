"""CLI runner shared by the standalone collector scripts."""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path


def run(module_name: str) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    parser = argparse.ArgumentParser()
    parser.add_argument("--lake-root")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    module = importlib.import_module(module_name)
    print(json.dumps(module.collect_once(lake_root=args.lake_root, dry_run=args.dry_run), default=str))
