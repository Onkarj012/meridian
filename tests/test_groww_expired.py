import importlib.util
import json
import os
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from ingest import groww_expired as groww
from ingest.envfile import load_env


ROOT = Path(__file__).resolve().parents[1]
CALENDAR_FIXTURE = ROOT / "tests/fixtures/upstox_contract_calendar.csv"
CLI_SPEC = importlib.util.spec_from_file_location("groww_expired_backfill", ROOT / "scripts/data/groww_expired_backfill.py")
assert CLI_SPEC and CLI_SPEC.loader
cli = importlib.util.module_from_spec(CLI_SPEC)
CLI_SPEC.loader.exec_module(cli)


def response(status, data):
    return groww.HttpResponse(status, json.dumps(data))


def request_transport(answer, calls=None):
    def transport(url, headers, method="GET", body=None):
        if calls is not None:
            calls.append((url, dict(headers), method, body))
        return answer(url, headers, method, body) if callable(answer) else answer
    return transport


def test_approval_checksum_known_vector():
    assert groww.approval_checksum("secret", "1719830400") == (
        "88a2717290e5a55d3180bc94e5a9258ae7df766924a6fc2f1c5cbd6e6804930a"
    )


def test_token_exchange_posts_documented_approval_shape():
    calls = []
    transport = request_transport(response(200, {"token": "issued-token"}), calls)
    assert groww.get_access_token(transport, "api-key", "api-secret", timestamp="1719830400", sleep=lambda _: None) == "issued-token"
    url, headers, method, body = calls[0]
    assert url == "https://api.groww.in/v1/token/api/access"
    assert method == "POST"
    assert headers["Authorization"] == "Bearer api-key"
    assert headers["Content-Type"] == "application/json"
    assert json.loads(body) == {
        "key_type": "approval",
        "checksum": groww.approval_checksum("api-secret", "1719830400"),
        "timestamp": "1719830400",
    }


def test_env_token_short_circuits_exchange(monkeypatch):
    monkeypatch.setenv("GROWW_ACCESS_TOKEN", "ready-token")
    called = []
    assert groww.get_access_token(lambda *args: called.append(args), None, None) == "ready-token"
    assert called == []


def test_expiry_request_uses_documented_payload_and_headers():
    calls = []
    transport = request_transport(response(200, {"status": "SUCCESS", "payload": {"expiries": ["2024-11-28"]}}), calls)
    assert groww.list_expiries(access_token="token", year=2024, month=11, transport=transport, sleep=lambda _: None) == ["2024-11-28"]
    query = parse_qs(urlparse(calls[0][0]).query)
    assert query == {"exchange": ["NSE"], "underlying_symbol": ["NIFTY"], "year": ["2024"], "month": ["11"]}
    assert calls[0][1]["Authorization"] == "Bearer token"
    assert calls[0][1]["X-API-VERSION"] == "1.0"


def test_contract_resolution_picks_futures_row():
    transport = request_transport(response(200, {"status": "SUCCESS", "payload": {"contracts": [
        "NSE-NIFTY-02Jan25-24000-CE", "NSE-NIFTY-26Dec24-FUT", "NSE-NIFTY-02Jan25-24000-PE",
    ]}}))
    assert groww.get_future_contract("2024-12-26", access_token="t", transport=transport, sleep=lambda _: None) == "NSE-NIFTY-26Dec24-FUT"


def test_contract_shape_difference_is_actionable():
    transport = request_transport(response(200, {"status": "SUCCESS", "payload": {"contracts": {}}}))
    with pytest.raises(groww.GrowwApiError, match="payload.contracts string list"):
        groww.list_contracts("2024-12-26", access_token="t", transport=transport, sleep=lambda _: None)


def test_contract_without_future_surfaces_live_verification_error():
    transport = request_transport(response(200, {"status": "SUCCESS", "payload": {"contracts": ["NSE-NIFTY-24000-CE"]}}))
    with pytest.raises(groww.GrowwApiError, match="no NIFTY futures contract"):
        groww.get_future_contract("2024-12-26", access_token="t", transport=transport, sleep=lambda _: None)


def test_resolve_contract_runs_expiry_then_contract_discovery():
    calls = []
    def answer(url, *_):
        calls.append(url)
        if "expiries" in url:
            return response(200, {"status": "SUCCESS", "payload": {"expiries": ["2024-12-26"]}})
        return response(200, {"status": "SUCCESS", "payload": {"contracts": ["NSE-NIFTY-26Dec24-FUT"]}})
    assert groww.resolve_future_contract("2024-12-26", access_token="t", transport=request_transport(answer), sleep=lambda _: None) == "NSE-NIFTY-26Dec24-FUT"
    assert ["expiries" in url for url in calls] == [True, False]


def test_day_chunks_use_30_day_inclusive_boundaries():
    assert groww.day_chunks("2024-01-01", "2024-02-14") == [
        (date(2024, 1, 1), date(2024, 1, 30)),
        (date(2024, 1, 31), date(2024, 2, 14)),
    ]


def test_fetch_candles_makes_two_requests_for_45_day_range():
    calls = []
    def answer(url, *_):
        calls.append(url)
        return response(200, {"status": "SUCCESS", "payload": {"candles": []}})
    groww.fetch_minute_candles("NSE-NIFTY-26Dec24-FUT", "2024-01-01", "2024-02-14", access_token="t", transport=request_transport(answer), sleep=lambda _: None)
    assert len(calls) == 2
    assert [parse_qs(urlparse(url).query)["start_time"][0] for url in calls] == ["2024-01-01 09:15:00", "2024-01-31 09:15:00"]
    assert [parse_qs(urlparse(url).query)["end_time"][0] for url in calls] == ["2024-01-30 15:30:00", "2024-02-14 15:30:00"]


def test_fetch_candles_deduplicates_and_preserves_oi_index_six():
    candles = [
        ["2024-01-02T09:16:00", 2, 3, 1, 2, 20, 222],
        ["2024-01-02T09:15:00", 1, 2, 0, 1, 10, 111],
        ["2024-01-02T09:15:00", 9, 9, 9, 9, 9, 999],
    ]
    transport = request_transport(response(200, {"status": "SUCCESS", "payload": {"candles": candles}}))
    out = groww.fetch_minute_candles("NSE-NIFTY-26Dec24-FUT", "2024-01-02", "2024-01-02", access_token="t", transport=transport, sleep=lambda _: None)
    assert [row[0] for row in out] == ["2024-01-02T09:15:00", "2024-01-02T09:16:00"]
    assert out[0][6] == 999


def test_candle_conversion_interprets_groww_naive_timestamps_as_ist_and_filters_session():
    out = groww.candles_to_futures_csv([
        ["2024-01-02T09:14:00", 0, 0, 0, 0, 0, 1],
        ["2024-01-02T09:15:00", 1.23456, 2, 1, 1.99999, 3.9, 888],
        ["2024-01-02T15:30:00", 2, 2, 2, 2, 4, 889],
        ["2024-01-02T15:31:00", 0, 0, 0, 0, 0, 0],
    ])
    assert out["time"].tolist() == ["09:15:00", "15:30:00"]
    assert out.iloc[0]["oi"] == 888
    assert out.iloc[0]["open"] == 1.2346


def test_candle_shape_difference_is_actionable():
    transport = request_transport(response(200, {"status": "SUCCESS", "payload": {"candles": [["bad"]]}}))
    with pytest.raises(groww.GrowwApiError, match="candle row differs"):
        groww.fetch_minute_candles("NSE-NIFTY-26Dec24-FUT", "2024-01-02", "2024-01-02", access_token="t", transport=transport, sleep=lambda _: None)


def test_retry_429_uses_exponential_backoff():
    answers = iter([response(429, {"status": "ERROR", "message": "slow"}), response(200, {"status": "SUCCESS", "payload": {"expiries": []}})])
    pauses = []
    assert groww.list_expiries(access_token="t", transport=lambda *args: next(answers), sleep=pauses.append) == []
    assert pauses == [0.5, 0.5]


def test_retry_5xx_exhaustion_is_bounded():
    pauses = []
    with pytest.raises(groww.GrowwApiError):
        groww.list_expiries(access_token="t", transport=lambda *args: response(500, {"message": "down"}), sleep=pauses.append)
    assert pauses == [0.5, 1.0, 2.0, 4.0, 8.0]


def test_env_loader_strips_quotes_and_existing_environment_wins(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('NEW_KEY="new value"\nKEEP=from-file\n# comment\nEMPTY=\n', encoding="utf-8")
    monkeypatch.setenv("KEEP", "from-process")
    loaded = load_env(env)
    assert loaded == {"NEW_KEY": "new value", "EMPTY": ""}
    assert os.environ["KEEP"] == "from-process"
    assert os.environ["NEW_KEY"] == "new value"


def test_dry_run_plan_has_no_network_and_writes_report(tmp_path, capsys, monkeypatch):
    called = []
    monkeypatch.setattr(groww, "urllib_transport", lambda *args: called.append(args))
    report = tmp_path / "report.json"
    assert cli.main([
        "--dry-run", "--calendar", str(CALENDAR_FIXTURE), "--from", "2024-11-01", "--to", "2024-11-05",
        "--out-root", str(tmp_path / "out"), "--report", str(report),
    ]) == 0
    output = capsys.readouterr().out
    assert "Groww expired futures backfill: 3 days in 1 contracts" in output
    assert "dry-run: no network calls or archive writes" in output
    assert called == []
    assert report.exists()


def test_probe_prints_count_first_last_and_oi_signal(capsys):
    def answer(url, *_):
        if "expiries" in url:
            return response(200, {"status": "SUCCESS", "payload": {"expiries": ["2024-12-26"]}})
        if "contracts" in url:
            return response(200, {"status": "SUCCESS", "payload": {"contracts": ["NSE-NIFTY-26Dec24-FUT"]}})
        return response(200, {"status": "SUCCESS", "payload": {"candles": [
            ["2024-12-26T09:15:00", 1, 1, 1, 1, 1, 7], ["2024-12-26T09:16:00", 1, 1, 1, 1, 1, 7],
        ]}})
    result = groww.probe("2024-12-26", access_token="t", transport=request_transport(answer), sleep=lambda _: None)
    output = capsys.readouterr().out
    assert result["bar_count"] == 2
    assert "bars=2" in output
    assert "oi_nonzero=True" in output
    assert "first=" in output and "last=" in output


def test_probe_mode_cli_does_not_require_calendar(monkeypatch, capsys):
    monkeypatch.setenv("GROWW_ACCESS_TOKEN", "ready")
    monkeypatch.setattr(cli, "probe", lambda expiry, **kwargs: print(f"probe expiry={expiry}"))
    assert cli.main(["--probe", "2024-12-26"]) == 0
    assert "probe expiry=2024-12-26" in capsys.readouterr().out
