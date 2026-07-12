"""Immutable, hash-chained decision envelopes for Sleeve F serving parity."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
import fcntl
import hashlib
import json
import os
from pathlib import Path
from typing import Literal


SCHEMA_VERSION = "meridian.decision-envelope.v1"
Mode = Literal["replay", "shadow", "live"]
PolicyDecision = Literal["trade", "no_trade", "blocked"]

# This is deliberately independent of dataclass introspection: it is the
# registered, ordered name/type manifest used for the schema identity.
ENVELOPE_SCHEMA_FIELDS: tuple[tuple[str, str], ...] = (
    ("schema_version", "str"),
    ("decision_id", "str"), ("signal_id", "str"),
    ("protocol_version", "str"), ("candidate_id", "str"), ("environment", "str"), ("mode", "Literal[replay,shadow,live]"),
    ("session_date", "str"), ("instrument_id", "str"), ("contract_id", "str"), ("decision_bar_id", "int"),
    ("exchange_ts", "str"), ("receive_ts", "str"), ("process_ts", "str"), ("clock_offset_ms", "float"), ("latency_ms", "float"), ("latency_budget_ms", "float"), ("clock_sync_ok", "bool"),
    ("raw_input_snapshot_id", "str"), ("raw_input_hash", "str"), ("feature_vector_hash", "str"), ("feature_set_hash", "str"), ("missing_required_features", "tuple[str,...]"), ("invalid_feature_flags", "tuple[str,...]"), ("feature_canary_flags", "tuple[str,...]"), ("data_health_state", "str"),
    ("code_commit", "str"), ("container_digest", "str"), ("model_version", "str"), ("model_digest", "str"), ("replay_version", "str"), ("config_version", "str"), ("config_digest", "str"),
    ("controller_version", "str"), ("controller_state_hash", "str"), ("controller_computed_ts", "str"), ("controller_cohort_session_ids", "tuple[str,...]"), ("controller_cohort_hash", "str"), ("controller_target_trades", "float"), ("controller_realized_trades", "float"),
    ("score_net_r", "float"), ("threshold_active", "float"), ("ranking_margin", "float"), ("ranking_margin_z60", "float|None"),
    ("eligibility_pass", "bool"), ("eligibility_fail_reasons", "tuple[str,...]"), ("would_trade", "bool"), ("policy_decision", "Literal[trade,no_trade,blocked]"), ("policy_block_reasons", "tuple[str,...]"),
    ("position_state_before", "str"), ("risk_state_version", "str"), ("available_capital", "float"), ("open_exposure", "float"), ("session_realized_r_before", "float"), ("daily_halt_state", "str"),
    ("manual_override_id", "str|None"), ("supersedes_decision_id", "str|None"), ("previous_envelope_hash", "str|None"), ("envelope_hash", "str"), ("signature_id", "str|None"), ("audit_write_status", "str"),
)


@dataclass(frozen=True)
class DecisionEnvelope:
    # Identity
    schema_version: str
    decision_id: str
    signal_id: str
    protocol_version: str
    candidate_id: str
    environment: str
    mode: Mode

    # Timestamps / clock
    session_date: str
    instrument_id: str
    contract_id: str
    decision_bar_id: int
    exchange_ts: str
    receive_ts: str
    process_ts: str
    clock_offset_ms: float
    latency_ms: float
    latency_budget_ms: float
    clock_sync_ok: bool

    # Input integrity
    raw_input_snapshot_id: str
    raw_input_hash: str
    feature_vector_hash: str
    feature_set_hash: str
    missing_required_features: tuple[str, ...]
    invalid_feature_flags: tuple[str, ...]
    feature_canary_flags: tuple[str, ...]
    data_health_state: str

    # Code / model / config versions
    code_commit: str
    container_digest: str
    model_version: str
    model_digest: str
    replay_version: str
    config_version: str
    config_digest: str

    # Controller block
    controller_version: str
    controller_state_hash: str
    controller_computed_ts: str
    controller_cohort_session_ids: tuple[str, ...]
    controller_cohort_hash: str
    controller_target_trades: float
    controller_realized_trades: float

    # Score / threshold
    score_net_r: float
    threshold_active: float
    ranking_margin: float
    ranking_margin_z60: float | None

    # Eligibility / policy
    eligibility_pass: bool
    eligibility_fail_reasons: tuple[str, ...]
    would_trade: bool
    policy_decision: PolicyDecision
    policy_block_reasons: tuple[str, ...]

    # Risk state
    position_state_before: str
    risk_state_version: str
    available_capital: float
    open_exposure: float
    session_realized_r_before: float
    daily_halt_state: str

    # Override / chain
    manual_override_id: str | None
    supersedes_decision_id: str | None
    previous_envelope_hash: str | None
    envelope_hash: str
    signature_id: str | None
    audit_write_status: str

    def __post_init__(self) -> None:
        for name in _TUPLE_FIELDS:
            object.__setattr__(self, name, tuple(getattr(self, name)))


_TUPLE_FIELDS = {
    "missing_required_features", "invalid_feature_flags", "feature_canary_flags",
    "controller_cohort_session_ids", "eligibility_fail_reasons", "policy_block_reasons",
}
_HASH_EXCLUDED = {"envelope_hash", "audit_write_status"}
# audit_write_status is a set-after-seal write outcome, intentionally unhashed.

_SCHEMA_FIELD_NAMES = tuple(name for name, _ in ENVELOPE_SCHEMA_FIELDS)
_DATACLASS_FIELD_NAMES = tuple(field.name for field in fields(DecisionEnvelope))
assert _SCHEMA_FIELD_NAMES == _DATACLASS_FIELD_NAMES, (
    f"ENVELOPE_SCHEMA_FIELDS names/order must match DecisionEnvelope fields: "
    f"{_SCHEMA_FIELD_NAMES!r} vs {_DATACLASS_FIELD_NAMES!r}"
)


def _canonical_json(value: object) -> str:
    """Canonical JSON whose float representation is Python's exact ``repr``."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def envelope_schema_hash() -> str:
    return hashlib.sha256(_canonical_json(ENVELOPE_SCHEMA_FIELDS).encode("utf-8")).hexdigest()


def _envelope_payload(envelope: DecisionEnvelope, *, include_hash: bool) -> dict[str, object]:
    values = asdict(envelope)
    names = [field.name for field in fields(DecisionEnvelope)]
    return {name: values[name] for name in names if include_hash or name not in _HASH_EXCLUDED}


def compute_envelope_hash(envelope: DecisionEnvelope) -> str:
    return hashlib.sha256(_canonical_json(_envelope_payload(envelope, include_hash=False)).encode("utf-8")).hexdigest()


def to_json_line(envelope: DecisionEnvelope) -> str:
    return _canonical_json(_envelope_payload(envelope, include_hash=True)) + "\n"


def from_json_line(line: str) -> DecisionEnvelope:
    data = json.loads(line)
    expected = tuple(field.name for field in fields(DecisionEnvelope))
    if tuple(data) != expected:
        raise ValueError("decision envelope JSON keys do not match the registered schema order")
    return DecisionEnvelope(**data)


def verify_chain(envelopes: list[DecisionEnvelope] | tuple[DecisionEnvelope, ...]) -> list[str]:
    """Return hash-link and content-hash violations in input order."""
    violations: list[str] = []
    previous: dict[tuple[str, str], str] = {}
    for index, envelope in enumerate(envelopes):
        label = f"envelope[{index}] decision_id={envelope.decision_id}"
        if envelope.envelope_hash != compute_envelope_hash(envelope):
            violations.append(f"{label}: envelope_hash does not match contents")
        key = (envelope.instrument_id, envelope.mode)
        expected_previous = previous.get(key)
        if envelope.previous_envelope_hash != expected_previous:
            violations.append(f"{label}: previous_envelope_hash does not match {key!r} chain")
        previous[key] = envelope.envelope_hash
    return violations


class EnvelopeWriter:
    """Durable append-only JSONL writer with per-instrument/mode chain state."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._previous: dict[tuple[str, str], str] = {}
        self._last_bar: dict[tuple[str, str, str], int] = {}
        self._decision_ids: set[str] = set()
        if self.path.exists():
            self._load_existing()

    def _load_existing(self) -> None:
        records: list[DecisionEnvelope] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    raise ValueError(f"blank line in append-only envelope log at {line_number}")
                records.append(from_json_line(line))
        violations = verify_chain(records)
        if violations:
            raise ValueError("cannot append to invalid envelope log: " + "; ".join(violations))
        for envelope in records:
            if envelope.decision_id in self._decision_ids:
                raise ValueError("existing envelope log contains duplicate decision_id")
            self._decision_ids.add(envelope.decision_id)
            self._previous[(envelope.instrument_id, envelope.mode)] = envelope.envelope_hash
            key = (envelope.session_date, envelope.instrument_id, envelope.mode)
            last = self._last_bar.get(key)
            if last is not None and envelope.decision_bar_id <= last:
                raise ValueError("existing envelope log violates decision_bar_id monotonicity")
            self._last_bar[key] = envelope.decision_bar_id

    def append(self, envelope: DecisionEnvelope) -> DecisionEnvelope:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_name(self.path.name + ".lock").open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                self._previous = {}
                self._last_bar = {}
                self._decision_ids = set()
                if self.path.exists():
                    self._load_existing()
                if envelope.schema_version != SCHEMA_VERSION:
                    raise ValueError(f"unexpected schema_version: {envelope.schema_version}")
                if envelope.decision_id in self._decision_ids:
                    raise ValueError("decision_id already exists in append-only log")
                key = (envelope.session_date, envelope.instrument_id, envelope.mode)
                last = self._last_bar.get(key)
                if last is not None and envelope.decision_bar_id <= last:
                    raise ValueError("decision_bar_id must increase within (session_date, instrument_id, mode)")
                chain_key = (envelope.instrument_id, envelope.mode)
                expected_previous = self._previous.get(chain_key)
                if envelope.previous_envelope_hash not in (None, expected_previous):
                    raise ValueError("previous_envelope_hash conflicts with writer chain state")
                resolved = replace(envelope, previous_envelope_hash=expected_previous)
                resolved = replace(resolved, envelope_hash=compute_envelope_hash(resolved))
                with self.path.open("a", encoding="utf-8", newline="") as handle:
                    handle.write(to_json_line(resolved))
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        self._previous[chain_key] = resolved.envelope_hash
        self._last_bar[key] = resolved.decision_bar_id
        self._decision_ids.add(resolved.decision_id)
        return resolved
