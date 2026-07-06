"""Append immutable run manifests."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def append_run_manifest(path: str | Path, record: dict) -> dict:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    row = {"created_at": datetime.now().isoformat(timespec="seconds"), **record}
    with out.open("a") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return row
