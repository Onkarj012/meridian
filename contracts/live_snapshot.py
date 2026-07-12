"""Immutable, hash-verified live decision snapshots for offline parity checks."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
import hashlib
import fcntl
import json
import math
import os
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from contracts.decision_envelope import PolicyDecision


LIVE_SNAPSHOT_SCHEMA_VERSION = "meridian.live-snapshot.v1"


@dataclass(frozen=True)
class LiveSnapshot:
    """Inputs and observed output of one live decision, captured atomically.

    ``bar_window`` contains JSON-compatible minute-bar records.  The frozen
    outer tuple preserves the exact input sequence; JSON serialization sorts
    mapping keys only for deterministic hashing.
    """

    snapshot_id: str
    session_date: str
    instrument_id: str
    contract_id: str
    captured_at_ts: str
    bar_window: tuple[Mapping[str, Any], ...]
    prior_session_close: float | None
    vix_t1: float | None
    days_to_expiry: int
    controller_state_hash: str
    threshold_active: float
    feature_vector: Mapping[str, float]
    score: float
    eligibility_pass: bool
    eligibility_fail_reasons: tuple[str, ...]
    policy_decision: PolicyDecision
    code_digest: str
    model_digest: str
    config_digest: str
    snapshot_hash: str = ""
    schema_version: str = LIVE_SNAPSHOT_SCHEMA_VERSION

    @property
    def auxiliary_inputs(self) -> Mapping[str, object]:
        """The auxiliary input block passed to offline callables."""
        return MappingProxyType(
            {
                "prior_session_close": self.prior_session_close,
                "vix_t1": self.vix_t1,
                "days_to_expiry": self.days_to_expiry,
                "controller_state_hash": self.controller_state_hash,
                "threshold_active": self.threshold_active,
            }
        )


_TUPLE_FIELDS = {"bar_window", "eligibility_fail_reasons"}


def _canonical_json(value: object) -> str:
    # ``_payload`` has already canonicalized its nested mappings.  Keep this
    # final pass order-preserving so the registered top-level schema order is
    # retained in JSONL.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _canonicalise(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _canonicalise(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, tuple | list):
        return [_canonicalise(item) for item in value]
    return value


def _payload(snapshot: LiveSnapshot, *, include_hash: bool) -> dict[str, object]:
    values = asdict(snapshot)
    payload = {
        field.name: values[field.name]
        for field in fields(LiveSnapshot)
        if include_hash or field.name != "snapshot_hash"
    }
    # Only nested mappings are canonicalized; field order is itself schema
    # identity and is intentionally retained in the emitted JSONL record.
    return {name: _canonicalise(value) for name, value in payload.items()}


def compute_snapshot_hash(snapshot: LiveSnapshot) -> str:
    """Return the SHA-256 identity of all snapshot content except its hash."""
    return hashlib.sha256(_canonical_json(_payload(snapshot, include_hash=False)).encode("utf-8")).hexdigest()


def to_json_line(snapshot: LiveSnapshot) -> str:
    return _canonical_json(_payload(snapshot, include_hash=True)) + "\n"


def from_json_line(line: str) -> LiveSnapshot:
    """Decode one snapshot line and reject reordered, malformed, or tampered data."""
    data = json.loads(line)
    expected = tuple(field.name for field in fields(LiveSnapshot))
    if tuple(data) != expected:
        raise ValueError("live snapshot JSON keys do not match the registered schema order")
    for name in _TUPLE_FIELDS:
        data[name] = tuple(data[name])
    snapshot = LiveSnapshot(**data)
    _validate_json_numbers(snapshot)
    if snapshot.schema_version != LIVE_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(f"unexpected schema_version: {snapshot.schema_version}")
    if snapshot.snapshot_hash != compute_snapshot_hash(snapshot):
        raise ValueError("snapshot_hash does not match contents")
    return snapshot


def _validate_json_numbers(snapshot: LiveSnapshot) -> None:
    numeric_values = [
        snapshot.prior_session_close,
        snapshot.vix_t1,
        snapshot.threshold_active,
        snapshot.score,
        *snapshot.feature_vector.values(),
    ]
    if any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in numeric_values if value is not None):
        raise ValueError("live snapshot numeric values must be finite")


def read_live_snapshots(path: str | Path) -> list[LiveSnapshot]:
    """Read and verify every record in an append-only snapshot JSONL log."""
    records: list[LiveSnapshot] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                raise ValueError(f"blank line in append-only live snapshot log at {line_number}")
            records.append(from_json_line(line))
    return records


# Short aliases make the JSONL contract convenient at capture and replay sites.
read_snapshots = read_live_snapshots


class LiveSnapshotWriter:
    """Durable append-only JSONL writer that validates existing snapshots."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._snapshot_ids: set[str] = set()
        if self.path.exists():
            for snapshot in read_live_snapshots(self.path):
                if snapshot.snapshot_id in self._snapshot_ids:
                    raise ValueError("existing live snapshot log contains duplicate snapshot_id")
                self._snapshot_ids.add(snapshot.snapshot_id)

    def append(self, snapshot: LiveSnapshot) -> LiveSnapshot:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_name(self.path.name + ".lock").open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                self._snapshot_ids = set()
                if self.path.exists():
                    for existing in read_live_snapshots(self.path):
                        if existing.snapshot_id in self._snapshot_ids:
                            raise ValueError("existing live snapshot log contains duplicate snapshot_id")
                        self._snapshot_ids.add(existing.snapshot_id)
                if snapshot.schema_version != LIVE_SNAPSHOT_SCHEMA_VERSION:
                    raise ValueError(f"unexpected schema_version: {snapshot.schema_version}")
                if snapshot.snapshot_id in self._snapshot_ids:
                    raise ValueError("snapshot_id already exists in append-only log")
                _validate_json_numbers(snapshot)
                resolved = replace(snapshot, snapshot_hash=compute_snapshot_hash(snapshot))
                with self.path.open("a", encoding="utf-8", newline="") as handle:
                    handle.write(to_json_line(resolved))
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        self._snapshot_ids.add(resolved.snapshot_id)
        return resolved


# Match the noun-first naming used by EnvelopeWriter while retaining a useful
# explicit name for callers that have both log types in scope.
SnapshotWriter = LiveSnapshotWriter
