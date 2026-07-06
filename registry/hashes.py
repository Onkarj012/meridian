"""Stable hash generation."""

import hashlib
import json

def compute_hash(value=None, **kwargs):
    payload = value if value is not None else kwargs
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
