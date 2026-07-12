import json
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ingest.collector_base import append_observation, dual_timestamp_record, latest_records
from ingest.collectors import adr_etf_closes, fii_dii, gift_nifty, global_minute, nse_announcements, option_chain, snapshot_ledger


def test_append_writer_is_idempotent_and_preserves_dual_timestamps(tmp_path):
    record = dual_timestamp_record({"instrument": "TEST", "close": 1.0}, "2026-07-12T09:15:00+05:30", receive_ts="2026-07-12T03:45:01Z")
    first = append_observation("example", [record], raw='{"close":1.0}', lake_root=tmp_path)
    data_path = tmp_path / "collectors/example" / first["file"]
    first_bytes = data_path.read_bytes()
    second = append_observation("example", [record], raw='{"close":1.0}', lake_root=tmp_path)

    assert first["action"] == "written"
    assert second["action"] == "skipped"
    rows = [json.loads(line) for line in (tmp_path / "collectors/example/manifest.jsonl").read_text().splitlines()]
    assert [row["action"] for row in rows] == ["written", "skipped"]
    assert data_path.read_bytes() == first_bytes
    stored = json.loads(first_bytes.decode().strip())
    for column in ("source_ts", "exchange_ts", "receive_ts"):
        assert datetime.fromisoformat(stored[column].replace("Z", "+00:00"))
    assert datetime.fromisoformat(record["source_ts"]).tzinfo
    assert datetime.fromisoformat(record["receive_ts"]).tzinfo == timezone.utc


def test_manifest_hash_matches_immutable_data_file(tmp_path):
    result = append_observation(
        "example", [dual_timestamp_record({"instrument": "TEST"}, "2026-07-12T09:15:00+05:30")],
        raw="payload", lake_root=tmp_path,
    )
    data_path = tmp_path / "collectors/example" / result["file"]
    assert result["sha256"] == hashlib.sha256(data_path.read_bytes()).hexdigest()
    assert (tmp_path / "collectors/example" / result["raw_file"]).read_text() == "payload"


def test_every_collector_exposes_a_complete_offline_dry_run_schema():
    collectors = [gift_nifty, global_minute, snapshot_ledger, nse_announcements, option_chain, adr_etf_closes, fii_dii]
    for collector in collectors:
        result = collector.collect_once(dry_run=True)
        assert result["action"] == "dry_run"
        assert set(collector.SCHEMA) == set(result["records"][0])
        assert {"source_ts", "exchange_ts", "receive_ts", "raw"} <= set(collector.SCHEMA)


def test_stub_collectors_accept_mocked_payloads_and_persist_full_schema(tmp_path):
    cases = [
        (gift_nifty, {"ts": "2026-07-12T09:15:00+05:30", "open": 1, "high": 2, "low": 0, "close": 1.5, "volume": 10}),
        (nse_announcements, {"symbol": "ABC", "headline": "Board meeting", "published_at": "2026-07-12T09:15:00+05:30", "available_at": "2026-07-12T09:16:00+05:30", "source": "NSE"}),
        (option_chain, {"underlying": "NIFTY", "underlying_value": 25000, "snapshot_ts": "2026-07-12T09:15:00+05:30", "expiry": "2026-07-30", "strike": 25000, "option_type": "CE", "bid": 1, "ask": 2, "ltp": 1.5, "oi": 4, "iv": 0.2, "volume": 5}),
        (adr_etf_closes, {"instrument": "INFY", "trade_date": "2026-07-11", "close": 20, "adj_close": 20, "currency": "USD", "record_type": "close", "provider": "mock"}),
        (fii_dii, {"date": "2026-07-11", "fii_buy": 1, "fii_sell": 2, "fii_net": -1, "dii_buy": 3, "dii_sell": 1, "dii_net": 2, "total_inst_net": 1, "released_at": "2026-07-11T18:30:00+05:30", "usable_from": "2026-07-12", "source": "mock"}),
    ]
    for collector, row in cases:
        result = collector.collect_once(lake_root=tmp_path, fetcher=lambda row=row: ([row], {"mock": row}))
        assert result["action"] == "written"
        manifest = tmp_path / "collectors" / collector.__name__.rsplit(".", 1)[-1] / "manifest.jsonl"
        entry = json.loads(manifest.read_text().splitlines()[0])
        stored = json.loads((manifest.parent / entry["file"]).read_text().splitlines()[0])
        assert set(collector.SCHEMA) <= set(stored)


def test_global_minute_uses_instrument_partitions_with_mocked_fetcher(tmp_path):
    rows = [
        {"instrument": "ES", "ts": "2026-07-12T03:45:00Z", "open": 1, "high": 2, "low": 0, "close": 1, "volume": 3, "provider": "mock"},
        {"instrument": "NQ", "ts": "2026-07-12T03:45:00Z", "open": 1, "high": 2, "low": 0, "close": 1, "volume": 3, "provider": "mock"},
    ]
    result = global_minute.collect_once(lake_root=tmp_path, fetcher=lambda: (rows, {"mock": rows}))
    assert result["records"] == 2
    root = tmp_path / "collectors/global_minute"
    assert list(root.glob("*/*/*/instrument=ES")) and list(root.glob("*/*/*/instrument=NQ"))


def test_global_minute_same_count_changed_value_is_not_deduplicated(tmp_path):
    def fetch(close):
        return ([{"instrument": "ES", "ts": "2026-07-12T03:45:00Z", "open": 1, "high": 2, "low": 0, "close": close, "volume": 3, "provider": "mock"}], {"rows": [{"close": close}]})

    first = global_minute.collect_once(lake_root=tmp_path, fetcher=lambda: fetch(1.0))
    second = global_minute.collect_once(lake_root=tmp_path, fetcher=lambda: fetch(2.0))
    assert first["batches"][0]["action"] == "written"
    assert second["batches"][0]["action"] == "written"


def test_latest_records_fails_closed_on_tampered_jsonl(tmp_path):
    result = append_observation(
        "example", [dual_timestamp_record({"instrument": "TEST"}, "2026-07-12T09:15:00+05:30")],
        raw="payload", lake_root=tmp_path,
    )
    path = tmp_path / "collectors/example" / result["file"]
    path.write_text(path.read_text().replace("TEST", "TAMPERED"), encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        latest_records("example", lake_root=tmp_path)


def test_snapshot_ledger_calculates_quote_age_from_local_lake(tmp_path):
    source_ts = datetime.now(timezone.utc) - timedelta(minutes=5)
    global_minute.collect_once(
        lake_root=tmp_path,
        fetcher=lambda: ([{"instrument": "ES", "ts": source_ts.isoformat(), "open": 1, "high": 2, "low": 0, "close": 1, "volume": 3, "provider": "mock"}], "mock"),
    )
    as_of = datetime.now(timezone.utc) + timedelta(minutes=1)
    result = snapshot_ledger.collect_once(lake_root=tmp_path, as_of=as_of)
    assert result["action"] == "written"
    manifest = tmp_path / "collectors/snapshot_ledger/manifest.jsonl"
    entry = json.loads(manifest.read_text().splitlines()[0])
    row = json.loads((manifest.parent / entry["file"]).read_text().splitlines()[0])
    assert row["source_collector"] == "global_minute"
    assert row["quote_age_seconds"] == pytest.approx(360, abs=2)
