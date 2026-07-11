import importlib.util
import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from ingest.upstox_expired import (
    HttpResponse, UpstoxApiError, candles_to_frame, candles_to_futures_csv,
    contract_key, expired_key, fetch_minute_candles, get_future_contract,
    list_expiries, minute_file_path, month_chunks, split_by_day,
    validate_day_file, write_day_csv,
)


ROOT = Path(__file__).resolve().parents[1]
CALENDAR_FIXTURE = ROOT / "tests/fixtures/upstox_contract_calendar.csv"
CLI_SPEC = importlib.util.spec_from_file_location("upstox_expired_backfill", ROOT / "scripts/data/upstox_expired_backfill.py")
assert CLI_SPEC and CLI_SPEC.loader
cli = importlib.util.module_from_spec(CLI_SPEC)
CLI_SPEC.loader.exec_module(cli)


def response(status, data):
    return HttpResponse(status, json.dumps(data))


def test_expired_key_accepts_dates_and_floatish_tokens():
    assert expired_key("12345.0", "2024-11-28") == "NSE_FO|12345|28-11-2024"
    assert expired_key(9, date(2025, 1, 30)) == "NSE_FO|9|30-01-2025"


def test_list_expiries_url_encodes_index_key_and_parses_objects():
    calls = []

    def transport(url, headers):
        calls.append((url, headers))
        return response(200, {"status": "success", "data": [{"expiry_date": "2024-11-28"}]})

    assert list_expiries(access_token="token", transport=transport, sleep=lambda _: None) == ["2024-11-28"]
    assert "NSE_INDEX%7CNifty%2050" in calls[0][0]
    assert calls[0][1]["Authorization"] == "Bearer token"


def test_get_future_contract_accepts_contract_list_shape():
    def transport(url, headers):
        return response(200, {"status": "success", "data": {"contracts": [{"exchange_token": "123", "instrument_key": "supplied"}]}})

    contract = get_future_contract("2024-11-28", access_token="token", transport=transport, sleep=lambda _: None)
    assert contract["exchange_token"] == "123"
    assert contract_key(contract, "123", "2024-11-28") == "supplied"


def test_month_chunks_are_inclusive_and_month_bounded():
    assert month_chunks("2024-01-30", "2024-03-02") == [
        (date(2024, 1, 30), date(2024, 1, 31)),
        (date(2024, 2, 1), date(2024, 2, 29)),
        (date(2024, 3, 1), date(2024, 3, 2)),
    ]


def test_month_chunks_reject_reverse_range():
    with pytest.raises(ValueError):
        month_chunks("2024-02-01", "2024-01-31")


def test_fetch_candles_chunks_deduplicates_and_sorts():
    calls = []

    def transport(url, headers):
        calls.append(url)
        if "2024-01-31" in url:
            candles = [["2024-01-31T10:00:00+05:30", 1, 2, 0, 1, 10, 100]]
        else:
            candles = [
                ["2024-02-01T10:01:00+05:30", 2, 3, 1, 2, 11, 101],
                ["2024-02-01T10:00:00+05:30", 1, 2, 0, 1, 10, 100],
            ]
        return response(200, {"status": "success", "data": {"candles": candles}})

    candles = fetch_minute_candles("NSE_FO|1|31-01-2024", "2024-01-31", "2024-02-01", access_token="t", transport=transport, sleep=lambda _: None)
    assert len(calls) == 2
    assert [c[0] for c in candles] == sorted(c[0] for c in candles)


def test_retry_429_uses_exponential_backoff():
    answers = iter([
        response(429, {"status": "error", "errors": [{"errorCode": "rate", "message": "slow"}]}),
        response(200, {"status": "success", "data": []}),
    ])
    pauses = []
    assert list_expiries(access_token="t", transport=lambda *_: next(answers), sleep=pauses.append) == []
    assert pauses == [0.5, 0.5]


def test_plus_plan_error_is_actionable():
    def transport(*_):
        return response(403, {"status": "error", "errors": [{"errorCode": "UDAPI1149", "message": "forbidden"}]})

    with pytest.raises(UpstoxApiError, match="Upstox Plus"):
        list_expiries(access_token="t", transport=transport, sleep=lambda _: None)


def test_candle_conversion_uses_ist_filters_rounds_and_sorts():
    candles = [
        ["2024-10-03T10:00:00+05:30", 1.23456, 2.34567, 1.11111, 1.99999, 3.9, 8.8],
        ["2024-10-03T03:45:00Z", 10.00009, 11, 9, 10.00008, 4, 12],
        ["2024-10-03T03:44:00Z", 99, 99, 99, 99, 1, 1],
        ["2024-10-03T15:31:00+05:30", 99, 99, 99, 99, 1, 1],
    ]
    out = candles_to_futures_csv(candles)
    assert out.columns.tolist() == ["date", "time", "symbol", "open", "high", "low", "close", "oi", "volume"]
    assert out["time"].tolist() == ["09:15:00", "10:00:00"]
    assert out.iloc[1]["open"] == 1.2346
    assert out.iloc[1]["oi"] == 8 and out.iloc[1]["volume"] == 3


def test_candles_to_frame_deduplicates_timestamp():
    frame = candles_to_frame([["2024-10-03T03:45:00Z", 1, 1, 1, 1, 1, 1], ["2024-10-03T03:45:00Z", 2, 2, 2, 2, 2, 2]])
    assert len(frame) == 1
    assert frame.iloc[0]["close"] == 1


def test_split_write_and_archive_path_layout(tmp_path):
    csv = pd.DataFrame({"date": ["2024-11-01", "2024-11-04"], "time": ["09:15:00", "09:15:00"], "symbol": ["NIFTY-I"] * 2, "open": [1, 1], "high": [1, 1], "low": [1, 1], "close": [1, 1], "oi": [1, 1], "volume": [1, 1]})
    days = split_by_day(csv)
    path = write_day_csv(days[date(2024, 11, 1)], date(2024, 11, 1), tmp_path)
    assert path == tmp_path / "2024/11/nifty_fut_01_11_2024.csv"
    assert minute_file_path("2024-11-01", tmp_path) == path


def test_writer_skips_existing_file_without_overwrite(tmp_path):
    frame = pd.DataFrame({"date": ["2024-11-01"], "time": ["09:15:00"], "symbol": ["NIFTY-I"], "open": [1], "high": [1], "low": [1], "close": [1], "oi": [1], "volume": [1]})
    assert write_day_csv(frame, "2024-11-01", tmp_path)
    assert write_day_csv(frame, "2024-11-01", tmp_path) is None


def valid_day_file(tmp_path, *, close=100.0, volume=10, oi=1000, count=375):
    timestamps = pd.date_range("2024-11-01 09:15:00", periods=count, freq="min")
    frame = pd.DataFrame({"date": timestamps.strftime("%Y-%m-%d"), "time": timestamps.strftime("%H:%M:%S"), "symbol": "NIFTY-I", "open": close, "high": close, "low": close, "close": close, "oi": oi, "volume": volume})
    return write_day_csv(frame, "2024-11-01", tmp_path)


def validation_row(**updates):
    row = {"trade_date": "2024-11-01", "front_close": 100, "front_volume": 3750, "front_oi": 1000}
    row.update(updates)
    return row


def test_validation_passes_at_tolerance_boundary(tmp_path):
    path = valid_day_file(tmp_path, close=100.1, volume=10, oi=1050)
    assert validate_day_file(path, validation_row(front_close=100, front_volume=3750, front_oi=1000))["ok"]


def test_validation_flags_bad_bar_count(tmp_path):
    report = validate_day_file(valid_day_file(tmp_path, count=349), validation_row())
    assert "bar_count" in report["failures"]


def test_validation_flags_bad_close(tmp_path):
    report = validate_day_file(valid_day_file(tmp_path, close=100.2), validation_row())
    assert "close" in report["failures"]


def test_validation_flags_bad_volume(tmp_path):
    report = validate_day_file(valid_day_file(tmp_path, volume=11), validation_row())
    assert "volume" in report["failures"]


def test_validation_flags_bad_oi(tmp_path):
    report = validate_day_file(valid_day_file(tmp_path, oi=1060), validation_row())
    assert "oi" in report["failures"]


def test_planning_uses_missing_calendar_files_and_groups_contracts(tmp_path):
    rows = cli.load_target_rows(CALENDAR_FIXTURE, None, "2024-11-01", "2024-11-05", tmp_path)
    assert len(rows) == 3  # 11-02 and 11-03 do not occur in fixture
    plan = cli.build_plan(rows)
    assert list(plan) == [("12345", "2024-11-28")]
    write_day_csv(pd.DataFrame(columns=["date", "time", "symbol", "open", "high", "low", "close", "oi", "volume"]), "2024-11-01", tmp_path)
    assert len(cli.load_target_rows(CALENDAR_FIXTURE, None, "2024-11-01", "2024-11-05", tmp_path)) == 2


def test_gap_days_are_an_alternative_target_source(tmp_path):
    gaps = tmp_path / "gaps.csv"
    gaps.write_text("trade_date,front_instrument_id,front_expiry\n2024-11-04,12345,2024-11-28\n", encoding="utf-8")
    rows = cli.load_target_rows(CALENDAR_FIXTURE, gaps, None, None, tmp_path)
    assert [row["trade_date"] for row in rows] == ["2024-11-04"]


def test_validate_missing_file_is_failure(tmp_path):
    assert validate_day_file(tmp_path / "none.csv", validation_row())["failures"] == ["missing_file"]
