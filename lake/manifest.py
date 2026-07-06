"""Lake manifest writer."""

import hashlib
import json
from pathlib import Path

def write_lake_manifest(path, payload=None, **metadata):
    path = Path(path)
    manifest = {**(payload or {}), **metadata}
    manifest.setdefault("format", "meridian.lake-manifest.v1")
    manifest["manifest_sha256"] = hashlib.sha256(json.dumps(manifest, sort_keys=True, default=str).encode()).hexdigest()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return manifest
