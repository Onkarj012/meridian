"""Immutable, hash-chained execution and risk append records for Sleeve F."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
import fcntl
import hashlib
import json
import os
from pathlib import Path


SCHEMA_VERSION = "meridian.execution-record.v1"

EXECUTION_SCHEMA_FIELDS: tuple[tuple[str, str], ...] = (
    ("schema_version", "str"),
    ("execution_event_id", "str"), ("decision_id", "str"), ("order_id", "str|None"), ("parent_order_id", "str|None"), ("position_id", "str|None"), ("execution_policy_version", "str"),
    ("sent_order", "bool"), ("operator_block_reason", "str|None"),
    ("intended_side", "str|None"), ("intended_type", "str|None"), ("intended_quantity", "float|None"), ("intended_price", "float|None"),
    ("broker_sent_ts", "str|None"), ("broker_ack_ts", "str|None"), ("broker_order_status", "str|None"), ("broker_reject_code", "str|None"), ("exchange_order_id", "str|None"),
    ("fill_records", "tuple[tuple[id,ts,qty,price,cumulative,remaining],...]"),
    ("cancel_request_ts", "str|None"), ("cancel_ack_ts", "str|None"),
    ("position_state_after", "str"),
    ("expected_spread_bps", "float|None"), ("realized_spread_bps", "float|None"), ("expected_slippage_bps", "float|None"), ("realized_slippage_bps", "float|None"), ("expected_fees_bps", "float|None"), ("realized_fees_bps", "float|None"), ("expected_taxes_bps", "float|None"), ("realized_taxes_bps", "float|None"), ("expected_total_cost_bps", "float|None"), ("realized_total_cost_bps", "float|None"),
    ("exit_reason", "str|None"), ("exit_ts", "str|None"), ("exit_price", "float|None"),
    ("gross_r", "float|None"), ("policy_net_r", "float|None"), ("operator_net_r", "float|None"), ("override_delta_r", "float|None"),
    ("broker_position", "str"), ("internal_position", "str"), ("reconciliation_status", "str"), ("reconciliation_ts", "str|None"),
    ("correction_reason", "str|None"), ("supersedes_execution_event_id", "str|None"), ("previous_event_hash", "str|None"), ("event_hash", "str"), ("signature_id", "str|None"),
)


@dataclass(frozen=True)
class ExecutionEvent:
    # Identity / order lifecycle
    schema_version: str
    execution_event_id: str
    decision_id: str
    order_id: str | None
    parent_order_id: str | None
    position_id: str | None
    execution_policy_version: str
    sent_order: bool
    operator_block_reason: str | None

    # Intended order and broker lifecycle
    intended_side: str | None
    intended_type: str | None
    intended_quantity: float | None
    intended_price: float | None
    broker_sent_ts: str | None
    broker_ack_ts: str | None
    broker_order_status: str | None
    broker_reject_code: str | None
    exchange_order_id: str | None
    fill_records: tuple[tuple[str, str, float, float, float, float], ...]
    cancel_request_ts: str | None
    cancel_ack_ts: str | None
    position_state_after: str

    # Expected vs realized costs
    expected_spread_bps: float | None
    realized_spread_bps: float | None
    expected_slippage_bps: float | None
    realized_slippage_bps: float | None
    expected_fees_bps: float | None
    realized_fees_bps: float | None
    expected_taxes_bps: float | None
    realized_taxes_bps: float | None
    expected_total_cost_bps: float | None
    realized_total_cost_bps: float | None

    # Exit and return attribution
    exit_reason: str | None
    exit_ts: str | None
    exit_price: float | None
    gross_r: float | None
    policy_net_r: float | None
    operator_net_r: float | None
    override_delta_r: float | None

    # Position reconciliation / correction chain
    broker_position: str
    internal_position: str
    reconciliation_status: str
    reconciliation_ts: str | None
    correction_reason: str | None
    supersedes_execution_event_id: str | None
    previous_event_hash: str | None
    event_hash: str
    signature_id: str | None

    def __post_init__(self) -> None:
        for name in _TUPLE_FIELDS:
            value = getattr(self, name)
            object.__setattr__(self, name, tuple(tuple(fill) for fill in value))


_TUPLE_FIELDS = {"fill_records"}
_HASH_EXCLUDED = {"event_hash"}


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def execution_schema_hash() -> str:
    return hashlib.sha256(_canonical_json(EXECUTION_SCHEMA_FIELDS).encode("utf-8")).hexdigest()


def _event_payload(event: ExecutionEvent, *, include_hash: bool) -> dict[str, object]:
    values = asdict(event)
    names = [field.name for field in fields(ExecutionEvent)]
    return {name: values[name] for name in names if include_hash or name not in _HASH_EXCLUDED}


def compute_event_hash(event: ExecutionEvent) -> str:
    return hashlib.sha256(_canonical_json(_event_payload(event, include_hash=False)).encode("utf-8")).hexdigest()


def to_json_line(event: ExecutionEvent) -> str:
    return _canonical_json(_event_payload(event, include_hash=True)) + "\n"


def from_json_line(line: str) -> ExecutionEvent:
    data = json.loads(line)
    expected = tuple(field.name for field in fields(ExecutionEvent))
    if tuple(data) != expected:
        raise ValueError("execution event JSON keys do not match the registered schema order")
    return ExecutionEvent(**data)


def verify_chain(events: list[ExecutionEvent] | tuple[ExecutionEvent, ...]) -> list[str]:
    violations: list[str] = []
    previous: str | None = None
    for index, event in enumerate(events):
        label = f"event[{index}] execution_event_id={event.execution_event_id}"
        if event.event_hash != compute_event_hash(event):
            violations.append(f"{label}: event_hash does not match contents")
        if event.previous_event_hash != previous:
            violations.append(f"{label}: previous_event_hash does not match event chain")
        previous = event.event_hash
    return violations


class ExecutionWriter:
    """Durable append-only JSONL writer for the global execution event chain."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._previous: str | None = None
        self._event_ids: set[str] = set()
        if self.path.exists():
            self._load_existing()

    def _load_existing(self) -> None:
        events: list[ExecutionEvent] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    raise ValueError(f"blank line in append-only execution log at {line_number}")
                events.append(from_json_line(line))
        violations = verify_chain(events)
        if violations:
            raise ValueError("cannot append to invalid execution log: " + "; ".join(violations))
        for event in events:
            if event.execution_event_id in self._event_ids:
                raise ValueError("existing execution log contains duplicate execution_event_id")
            self._event_ids.add(event.execution_event_id)
        if events:
            self._previous = events[-1].event_hash

    def append(self, event: ExecutionEvent) -> ExecutionEvent:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_name(self.path.name + ".lock").open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                self._previous = None
                self._event_ids = set()
                if self.path.exists():
                    self._load_existing()
                if event.schema_version != SCHEMA_VERSION:
                    raise ValueError(f"unexpected schema_version: {event.schema_version}")
                if event.execution_event_id in self._event_ids:
                    raise ValueError("execution_event_id already exists in append-only log")
                if event.previous_event_hash not in (None, self._previous):
                    raise ValueError("previous_event_hash conflicts with writer chain state")
                resolved = replace(event, previous_event_hash=self._previous)
                resolved = replace(resolved, event_hash=compute_event_hash(resolved))
                with self.path.open("a", encoding="utf-8", newline="") as handle:
                    handle.write(to_json_line(resolved))
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        self._previous = resolved.event_hash
        self._event_ids.add(resolved.execution_event_id)
        return resolved
