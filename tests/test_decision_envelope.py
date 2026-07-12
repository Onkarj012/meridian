from __future__ import annotations

from dataclasses import fields, replace
from pathlib import Path

import pytest

from contracts.decision_envelope import (
    SCHEMA_VERSION,
    DecisionEnvelope,
    EnvelopeWriter,
    compute_envelope_hash,
    envelope_schema_hash,
    from_json_line,
    to_json_line,
    verify_chain,
)


FIXTURE = Path(__file__).parent / "fixtures" / "decision_envelope_golden.jsonl"
GOLDEN_HASH = "1743fae8b6a6cc00afff740865d5db54cecae3fc0d51afc4b8a5a055e7f1dea8"
SCHEMA_HASH = "27637b07ffbd1a9e5538c6f509e5ed2a44a6e98fd4a5eaf3073e2d1afc584485"


def envelope(*, decision_id: str = "decision-001", bar_id: int = 1, mode: str = "replay") -> DecisionEnvelope:
    record = DecisionEnvelope(
        schema_version=SCHEMA_VERSION,
        decision_id=decision_id,
        signal_id="signal-001",
        protocol_version="protocol-v1",
        candidate_id="candidate-101",
        environment="test",
        mode=mode,  # type: ignore[arg-type]
        session_date="2026-07-12",
        instrument_id="NIFTY-FUT",
        contract_id="NIFTY26JULFUT",
        decision_bar_id=bar_id,
        exchange_ts="2026-07-12T09:20:00.123456+05:30",
        receive_ts="2026-07-12T09:20:00.223456+05:30",
        process_ts="2026-07-12T09:20:00.323456+05:30",
        clock_offset_ms=0.125,
        latency_ms=12.345678901234567,
        latency_budget_ms=100.0,
        clock_sync_ok=True,
        raw_input_snapshot_id="snapshot-sha256",
        raw_input_hash="raw-sha256",
        feature_vector_hash="feature-vector-sha256",
        feature_set_hash="feature-set-sha256",
        missing_required_features=(),
        invalid_feature_flags=("none",),
        feature_canary_flags=("canary-a",),
        data_health_state="healthy",
        code_commit="abc123",
        container_digest="sha256:container",
        model_version="model-v1",
        model_digest="sha256:model",
        replay_version="replay-v1",
        config_version="config-v1",
        config_digest="sha256:config",
        controller_version="controller-v1",
        controller_state_hash="sha256:controller-state",
        controller_computed_ts="2026-07-12T08:59:00+05:30",
        controller_cohort_session_ids=("2026-07-10", "2026-07-11"),
        controller_cohort_hash="sha256:cohort",
        controller_target_trades=42.0,
        controller_realized_trades=41.0,
        score_net_r=0.12345678901234567,
        threshold_active=0.1,
        ranking_margin=0.02345678901234567,
        ranking_margin_z60=None,
        eligibility_pass=True,
        eligibility_fail_reasons=(),
        would_trade=True,
        policy_decision="trade",
        policy_block_reasons=(),
        position_state_before="flat",
        risk_state_version="risk-v1",
        available_capital=1_000_000.0,
        open_exposure=0.0,
        session_realized_r_before=-0.125,
        daily_halt_state="clear",
        manual_override_id=None,
        supersedes_decision_id=None,
        previous_envelope_hash=None,
        envelope_hash="",
        signature_id="signature-001",
        audit_write_status="pending",
    )
    return replace(record, envelope_hash=compute_envelope_hash(record))


def test_envelope_is_immutable_and_has_full_registered_field_list() -> None:
    record = envelope()
    assert len(fields(DecisionEnvelope)) == 61
    with pytest.raises(Exception):
        record.decision_id = "mutated"  # type: ignore[misc]


def test_direct_lists_are_shallow_frozen_before_hashing() -> None:
    values = ["feature-a"]
    record = replace(envelope(), missing_required_features=values, eligibility_fail_reasons=values)
    values.append("mutated")
    assert record.missing_required_features == ("feature-a",)
    assert record.eligibility_fail_reasons == ("feature-a",)
    assert compute_envelope_hash(record) == compute_envelope_hash(record)


def test_json_round_trip_is_byte_exact_and_golden_hash_is_stable() -> None:
    line = FIXTURE.read_text(encoding="utf-8")
    record = from_json_line(line)
    assert to_json_line(record) == line
    assert record.envelope_hash == GOLDEN_HASH
    assert compute_envelope_hash(record) == GOLDEN_HASH


def test_schema_hash_is_pinned() -> None:
    assert envelope_schema_hash() == SCHEMA_HASH


def test_chain_verification_catches_tampering() -> None:
    first = envelope()
    second_seed = envelope(decision_id="decision-002", bar_id=2)
    second = replace(second_seed, previous_envelope_hash=first.envelope_hash)
    second = replace(second, envelope_hash=compute_envelope_hash(second))
    assert verify_chain([first, second]) == []
    tampered = replace(second, score_net_r=9.0)
    assert any("envelope_hash" in issue for issue in verify_chain([first, tampered]))


def test_writer_is_append_only_monotonic_and_restart_safe(tmp_path: Path) -> None:
    path = tmp_path / "envelopes.jsonl"
    writer = EnvelopeWriter(path)
    first = writer.append(envelope())
    with pytest.raises(ValueError, match="decision_id"):
        writer.append(envelope())
    with pytest.raises(ValueError, match="decision_bar_id"):
        writer.append(envelope(decision_id="decision-rewrite", bar_id=1))
    restarted = EnvelopeWriter(path)
    second = restarted.append(envelope(decision_id="decision-002", bar_id=2))
    assert second.previous_envelope_hash == first.envelope_hash
    assert verify_chain([from_json_line(line) for line in path.read_text(encoding="utf-8").splitlines()]) == []


def test_two_writer_instances_re_read_chain_under_lock(tmp_path: Path) -> None:
    path = tmp_path / "envelopes.jsonl"
    first_writer, second_writer = EnvelopeWriter(path), EnvelopeWriter(path)
    first_writer.append(envelope())
    second_writer.append(envelope(decision_id="decision-002", bar_id=2))
    first_writer.append(envelope(decision_id="decision-003", bar_id=3))
    records = [from_json_line(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert verify_chain(records) == []
