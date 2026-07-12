from __future__ import annotations

from dataclasses import fields, replace
from pathlib import Path

import pytest

from contracts.execution_records import (
    SCHEMA_VERSION,
    ExecutionEvent,
    ExecutionWriter,
    compute_event_hash,
    execution_schema_hash,
    from_json_line,
    to_json_line,
    verify_chain,
)


SCHEMA_HASH = "6bae74fbf5fceb3dc1fb5ffdc40d7687da6c79242a4d75e17b3bf7af91aa5ecf"


def event(*, event_id: str = "execution-001", decision_id: str = "decision-001") -> ExecutionEvent:
    record = ExecutionEvent(
        schema_version=SCHEMA_VERSION,
        execution_event_id=event_id,
        decision_id=decision_id,
        order_id="order-001",
        parent_order_id=None,
        position_id="position-001",
        execution_policy_version="execution-v1",
        sent_order=True,
        operator_block_reason=None,
        intended_side="buy",
        intended_type="market",
        intended_quantity=50.0,
        intended_price=25000.125,
        broker_sent_ts="2026-07-12T09:20:01+05:30",
        broker_ack_ts="2026-07-12T09:20:01.050000+05:30",
        broker_order_status="filled",
        broker_reject_code=None,
        exchange_order_id="exchange-001",
        fill_records=(("fill-001", "2026-07-12T09:20:01.100000+05:30", 50.0, 25000.25, 50.0, 0.0),),
        cancel_request_ts=None,
        cancel_ack_ts=None,
        position_state_after="long",
        expected_spread_bps=0.2,
        realized_spread_bps=0.25,
        expected_slippage_bps=1.0,
        realized_slippage_bps=1.25,
        expected_fees_bps=0.1,
        realized_fees_bps=0.1,
        expected_taxes_bps=0.05,
        realized_taxes_bps=0.05,
        expected_total_cost_bps=1.35,
        realized_total_cost_bps=1.65,
        exit_reason=None,
        exit_ts=None,
        exit_price=None,
        gross_r=None,
        policy_net_r=None,
        operator_net_r=None,
        override_delta_r=0.0,
        broker_position="long:50",
        internal_position="long:50",
        reconciliation_status="matched",
        reconciliation_ts="2026-07-12T09:20:01.200000+05:30",
        correction_reason=None,
        supersedes_execution_event_id=None,
        previous_event_hash=None,
        event_hash="",
        signature_id="signature-001",
    )
    return replace(record, event_hash=compute_event_hash(record))


def test_event_is_immutable_and_has_full_registered_field_list() -> None:
    record = event()
    assert len(fields(ExecutionEvent)) == 48
    with pytest.raises(Exception):
        record.decision_id = "mutated"  # type: ignore[misc]


def test_direct_nested_lists_are_shallow_frozen_before_hashing() -> None:
    fills = [["fill-001", "2026-07-12T09:20:01.100000+05:30", 50.0, 25000.25, 50.0, 0.0]]
    record = replace(event(), fill_records=fills)
    fills[0][2] = 99.0
    assert record.fill_records == (tuple(fills[0][:2] + [50.0] + fills[0][3:]),)
    assert compute_event_hash(record) == compute_event_hash(record)


def test_json_round_trip_is_byte_exact_and_schema_hash_is_pinned() -> None:
    record = event()
    assert to_json_line(from_json_line(to_json_line(record))) == to_json_line(record)
    assert execution_schema_hash() == SCHEMA_HASH


def test_event_chain_verification_catches_tampering() -> None:
    first = event()
    next_seed = event(event_id="execution-002", decision_id="decision-002")
    second = replace(next_seed, previous_event_hash=first.event_hash)
    second = replace(second, event_hash=compute_event_hash(second))
    assert verify_chain([first, second]) == []
    tampered = replace(second, realized_fees_bps=99.0)
    assert any("event_hash" in issue for issue in verify_chain([first, tampered]))


def test_writer_restart_and_correction_supersedes_semantics(tmp_path: Path) -> None:
    path = tmp_path / "execution.jsonl"
    writer = ExecutionWriter(path)
    first = writer.append(event())
    with pytest.raises(ValueError, match="execution_event_id"):
        writer.append(event())
    correction = replace(
        event(event_id="execution-002"),
        correction_reason="broker fee correction",
        supersedes_execution_event_id=first.execution_event_id,
    )
    second = ExecutionWriter(path).append(correction)
    assert second.decision_id == first.decision_id
    assert second.supersedes_execution_event_id == first.execution_event_id
    assert second.previous_event_hash == first.event_hash
    assert verify_chain([from_json_line(line) for line in path.read_text(encoding="utf-8").splitlines()]) == []


def test_two_writer_instances_re_read_chain_under_lock(tmp_path: Path) -> None:
    path = tmp_path / "execution.jsonl"
    first_writer, second_writer = ExecutionWriter(path), ExecutionWriter(path)
    first_writer.append(event())
    second_writer.append(event(event_id="execution-002", decision_id="decision-002"))
    first_writer.append(event(event_id="execution-003", decision_id="decision-003"))
    records = [from_json_line(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert verify_chain(records) == []
