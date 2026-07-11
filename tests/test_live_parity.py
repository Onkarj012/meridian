from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from contracts.live_snapshot import (
    LIVE_SNAPSHOT_SCHEMA_VERSION,
    LiveSnapshot,
    LiveSnapshotWriter,
    compute_snapshot_hash,
    from_json_line,
    read_live_snapshots,
    to_json_line,
)
from evidence.live_parity import RecomputeResult, compare, recompute_from_snapshot


def snapshot(*, snapshot_id: str = "capture-001") -> LiveSnapshot:
    seed = LiveSnapshot(
        snapshot_id=snapshot_id,
        session_date="2026-07-12",
        instrument_id="NIFTY-FUT",
        contract_id="NIFTY26JULFUT",
        captured_at_ts="2026-07-12T09:20:00+05:30",
        bar_window=({"timestamp": "2026-07-12T09:19:00+05:30", "close": 100.0, "volume": 5},),
        prior_session_close=99.0,
        vix_t1=12.5,
        days_to_expiry=4,
        controller_state_hash="sha256:controller",
        threshold_active=0.1,
        feature_vector={"close": 100.0, "prior_close": 99.0},
        score=0.2,
        eligibility_pass=True,
        eligibility_fail_reasons=(),
        policy_decision="trade",
        code_digest="sha256:code",
        model_digest="sha256:model",
        config_digest="sha256:config",
        schema_version=LIVE_SNAPSHOT_SCHEMA_VERSION,
    )
    return replace(seed, snapshot_hash=compute_snapshot_hash(seed))


def exact_feature_fn(bars, auxiliary):
    return {"close": bars[-1]["close"], "prior_close": auxiliary["prior_session_close"]}


def exact_score_fn(features, auxiliary):
    return {
        "score": 0.2,
        "threshold_active": auxiliary["threshold_active"],
        "eligibility_pass": True,
        "eligibility_fail_reasons": (),
        "policy_decision": "trade",
    }


def recompute(record: LiveSnapshot, **overrides) -> RecomputeResult:
    result = recompute_from_snapshot(record, feature_fn=exact_feature_fn, score_fn=exact_score_fn)
    return replace(result, **overrides)


def test_exact_match_is_pass() -> None:
    report = compare(snapshot(), recompute(snapshot()))
    assert report.verdict == "PASS"
    assert report.score_absdiff == 0.0
    assert "Verdict: PASS" in report.to_markdown()


def test_score_drift_with_same_decision_is_degraded() -> None:
    record = snapshot()
    report = compare(record, recompute(record, score=record.score + 1e-8))
    assert report.verdict == "DEGRADED"


def test_decision_flip_is_fail() -> None:
    record = snapshot()
    report = compare(record, recompute(record, policy_decision="no_trade"))
    assert report.verdict == "FAIL"


def test_eligibility_reason_set_difference_is_fail() -> None:
    record = snapshot()
    report = compare(
        record,
        recompute(record, eligibility_pass=False, eligibility_fail_reasons=("stale_bar",)),
    )
    assert report.verdict == "FAIL"
    assert not report.eligibility_fail_reasons_equal


def test_digest_mismatch_is_fail_even_when_outputs_match() -> None:
    record = snapshot()
    report = compare(record, recompute(record, model_digest="sha256:other-model"))
    assert report.verdict == "FAIL"
    assert report.digest_mismatches == ("model_digest",)


def test_snapshot_hash_round_trip_and_tamper_detection() -> None:
    record = snapshot()
    line = to_json_line(record)
    assert from_json_line(line) == record
    tampered = line.replace('"score":0.2', '"score":0.3')
    with pytest.raises(ValueError, match="snapshot_hash"):
        from_json_line(tampered)


def test_writer_is_append_only_and_reader_validates_existing_log(tmp_path: Path) -> None:
    path = tmp_path / "live_snapshots.jsonl"
    writer = LiveSnapshotWriter(path)
    first = writer.append(snapshot())
    with pytest.raises(ValueError, match="snapshot_id"):
        writer.append(snapshot())
    second = writer.append(snapshot(snapshot_id="capture-002"))
    assert read_live_snapshots(path) == [first, second]
    restarted = LiveSnapshotWriter(path)
    with pytest.raises(ValueError, match="snapshot_id"):
        restarted.append(snapshot(snapshot_id="capture-002"))
