import json

import pytest

from ingest.optinet_data import (
    build_optinet_source_manifest,
    discover_optinet_sources,
    iter_equity_minute_file,
    iter_nifty_futures_minute_file,
    iter_nifty_spot_minute_file,
    normalize_equity_minute_row,
    normalize_index_futures_minute_row,
    normalize_index_spot_minute_row,
    summarize_minute_quality,
    validate_for_meridian_futures,
)


def test_equity_normalizer_and_iterator_derives_symbol(tmp_path):
    row = {"date": "2024-01-02 09:15:00", "open": "10", "high": "11.5", "low": "9", "close": "10.5", "volume": "1,200"}

    normalized = normalize_equity_minute_row(row, "RELIANCE", source_path="fixture.csv")

    assert normalized["symbol"] == "RELIANCE"
    assert normalized["timestamp"] == "2024-01-02T09:15:00+05:30"
    assert normalized["open"] == 10
    assert normalized["high"] == 11.5
    assert normalized["volume"] == 1200
    assert normalized["adjustment_status"] == "unknown_optinet_minute"

    csv_path = tmp_path / "RELIANCE_minute.csv"
    csv_path.write_text("date,open,high,low,close,volume\n2024-01-02 09:15:00,10,11,9,10.5,1200\n", encoding="utf-8")

    rows = list(iter_equity_minute_file(csv_path))

    assert rows[0]["symbol"] == "RELIANCE"
    assert rows[0]["source_path"] == str(csv_path)


def test_futures_normalizer_and_iterator_fields_are_stable(tmp_path):
    row = {
        "date": "02/01/2024",
        "time": "09:15:00",
        "symbol": "NIFTY-I",
        "open": "21000",
        "high": "21010",
        "low": "20990",
        "close": "21005",
        "oi": "500000",
        "volume": "750",
    }

    first = normalize_index_futures_minute_row(row, source_path="fut.csv")
    second = normalize_index_futures_minute_row(row, source_path="fut.csv")

    assert first["tradingsymbol"] == "NIFTY-I"
    assert first["instrument_type"] == "FUTIDX"
    assert first["expiry"] == "continuous_front_month"
    assert first["instrument_token"] == second["instrument_token"]
    assert first["timestamp"] == "2024-01-02T09:15:00+05:30"
    assert first["oi"] == 500000
    assert first["volume"] == 750

    csv_path = tmp_path / "nifty_fut_02_01_2024.csv"
    csv_path.write_text(
        "date,time,symbol,open,high,low,close,oi,volume\n"
        "2024-01-02,09:15,NIFTY-I,21000,21010,20990,21005,500000,750\n",
        encoding="utf-8",
    )

    rows = list(iter_nifty_futures_minute_file(csv_path))

    assert rows[0]["tradingsymbol"] == "NIFTY-I"
    assert rows[0]["timestamp"] == "2024-01-02T09:15:00+05:30"


def test_futures_normalizer_allows_zero_oi_with_positive_volume():
    row = {
        "date": "02/01/2024",
        "time": "09:15:00",
        "symbol": "NIFTY-I",
        "open": "21000",
        "high": "21010",
        "low": "20990",
        "close": "21005",
        "oi": "0",
        "volume": "750",
    }

    normalized = normalize_index_futures_minute_row(row, source_path="fut.csv")

    assert normalized["oi"] == 0.0
    assert normalized["volume"] == 750


def test_futures_normalizer_allows_zero_volume():
    row = {
        "date": "02/01/2024",
        "time": "09:15:00",
        "symbol": "NIFTY-I",
        "open": "21000",
        "high": "21010",
        "low": "20990",
        "close": "21005",
        "oi": "500000",
        "volume": "0",
    }

    normalized = normalize_index_futures_minute_row(row, source_path="fut.csv")

    assert normalized["volume"] == 0
    assert normalized["oi"] == 500000


def test_futures_normalizer_rejects_negative_volume():
    row = {
        "date": "02/01/2024",
        "time": "09:15:00",
        "symbol": "NIFTY-I",
        "open": "21000",
        "high": "21010",
        "low": "20990",
        "close": "21005",
        "oi": "500000",
        "volume": "-1",
    }

    with pytest.raises(ValueError, match="volume must be >= 0"):
        normalize_index_futures_minute_row(row, source_path="fut.csv")


def test_spot_normalizer_allows_missing_and_zero_volume(tmp_path):
    row = {"date": "2024-01-02", "time": "09:15", "symbol": "NIFTY 50", "open": "21000", "high": "21010", "low": "20990", "close": "21005"}

    normalized = normalize_index_spot_minute_row(row)

    assert normalized["volume"] == 0
    assert normalized["context_only"] is True
    assert normalized["volume_status"] == "context_only_zero_volume_allowed"
    assert normalized["instrument_type"] == "INDEX"

    csv_path = tmp_path / "nifty_spot_02_01_2024.csv"
    csv_path.write_text(
        "date,time,symbol,open,high,low,close,volume\n"
        "2024-01-02,09:15,NIFTY 50,21000,21010,20990,21005,0\n",
        encoding="utf-8",
    )

    rows = list(iter_nifty_spot_minute_file(csv_path))

    assert rows[0]["volume"] == 0
    assert rows[0]["context_only"] is True


def test_discovery_and_manifest_use_toy_tree_and_write_json(tmp_path):
    data_root = tmp_path / "data"
    (data_root / "nifty500").mkdir(parents=True)
    (data_root / "option_data" / "nifty_data" / "nifty_fut" / "2024" / "1").mkdir(parents=True)
    (data_root / "option_data" / "nifty_data" / "nifty_spot").mkdir(parents=True)
    (data_root / "bhavcopy" / "cm").mkdir(parents=True)
    (data_root / "bhavcopy" / "fo").mkdir(parents=True)
    (data_root / "sentiment").mkdir(parents=True)
    (data_root / "indices").mkdir(parents=True)

    (data_root / "nifty500" / "RELIANCE_minute.csv").write_text(
        "date,open,high,low,close,volume\n2024-01-02 09:15:00,10,11,9,10.5,1200\n",
        encoding="utf-8",
    )
    (data_root / "option_data" / "nifty_data" / "nifty_fut" / "2024" / "1" / "nifty_fut_02_01_2024.csv").write_text(
        "date,time,symbol,open,high,low,close,oi,volume\n2024-01-02,09:15,NIFTY-I,1,2,1,2,10,20\n",
        encoding="utf-8",
    )
    (data_root / "bhavcopy" / "cm" / "cm.parquet").write_bytes(b"not-real-parquet")
    output_path = tmp_path / "manifest.json"

    discovery = discover_optinet_sources(data_root)
    manifest = build_optinet_source_manifest(data_root, output_path=output_path)

    assert discovery["families"]["nifty500"]["file_count"] == 1
    assert discovery["families"]["nifty_fut"]["file_count"] == 1
    assert discovery["families"]["bhavcopy_cm"]["file_count"] == 1
    assert manifest["manifest_id"]
    assert manifest["total_bytes"] > 0
    assert manifest["timestamp_boundaries"]["nifty500"][0]["first_timestamp"] == "2024-01-02T09:15:00+05:30"
    assert json.loads(output_path.read_text(encoding="utf-8"))["manifest_id"] == manifest["manifest_id"]


def test_summarize_minute_quality_counts_zero_volume_and_invalid_session():
    rows = [
        {"symbol": "RELIANCE", "timestamp": "2024-01-02T09:15:00+05:30", "volume": 0},
        {"symbol": "RELIANCE", "timestamp": "2024-01-02T15:31:00+05:30", "volume": 10},
        {"symbol": "NIFTY-I", "timestamp": "2024-01-02T10:00:00+05:30", "volume": 20, "oi": 0},
    ]

    summary = summarize_minute_quality(rows)

    assert summary["row_count"] == 3
    assert summary["zero_volume_count"] == 1
    assert summary["zero_volume_share"] == pytest.approx(1 / 3)
    assert summary["zero_oi_count"] == 1
    assert summary["zero_oi_share"] == pytest.approx(1 / 3)
    assert summary["invalid_session_count"] == 1
    assert summary["symbols"] == ["NIFTY-I", "RELIANCE"]


def test_invalid_futures_rejection_and_validation_errors():
    bad_row = {
        "date": "2024-01-02",
        "time": "09:15",
        "symbol": "NIFTY-I",
        "open": "21000",
        "high": "21010",
        "low": "20990",
        "close": "21005",
        "oi": "-5",
        "volume": "750",
    }

    with pytest.raises(ValueError, match="oi must be >= 0"):
        normalize_index_futures_minute_row(bad_row)

    validation = validate_for_meridian_futures(
        [
            {
                "timestamp": "2024-01-02T09:15:00+05:30",
                "tradingsymbol": "NIFTY-I",
                "instrument_type": "FUTIDX",
                "oi": -5,
                "volume": "",
            }
        ]
    )

    assert validation["valid"] is False
    assert any("oi must be >= 0" in error for error in validation["errors"])
    assert any("volume" in error for error in validation["errors"])


def test_validate_for_meridian_futures_accepts_zero_volume():
    validation = validate_for_meridian_futures(
        [
            {
                "timestamp": "2024-01-02T09:15:00+05:30",
                "tradingsymbol": "NIFTY-I",
                "instrument_type": "FUTIDX",
                "oi": 500000,
                "volume": 0,
            }
        ]
    )

    assert validation == {"valid": True, "row_count": 1, "errors": []}
