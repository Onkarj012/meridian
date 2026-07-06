from datetime import date

from ingest.fii_dii import load_as_features, parse_fii_dii_payload
from ingest.quality import freshness_stamp, gap_report, zero_volume_report


def test_zero_volume_report_flags_rows_and_symbols():
    df = {"symbol": ["NIFTY 50", "ABC", "ABC"], "volume": [0, 10, 0]}

    report = zero_volume_report(df)

    assert report.zero_volume_rows == 2
    assert report.symbols == ["ABC", "NIFTY 50"]


def test_gap_report_detects_missing_minute():
    df = {"date": ["2026-01-01 09:15:00", "2026-01-01 09:17:00"]}

    report = gap_report(df, freq="1min")

    assert report.expected_rows == 3
    assert report.missing_rows == 1
    assert report.first_missing == "2026-01-01T09:16:00"


def test_freshness_stamp_marks_stale_data():
    df = {"date": ["2026-01-01 09:15:00"]}

    stamp = freshness_stamp(
        df,
        source="kite",
        now="2026-01-01 10:15:01",
        max_age_seconds=3600,
    )

    assert stamp.stale is True
    assert stamp.age_seconds == 3601


def test_fii_dii_payload_is_usable_next_day():
    payload = [
        {"category": "FII/FPI", "date": "03-Jul-2026", "buyValue": "1,000", "sellValue": "900", "netValue": "100"},
        {"category": "DII", "date": "03-Jul-2026", "buyValue": "700", "sellValue": "750", "netValue": "-50"},
    ]

    df = parse_fii_dii_payload(payload)
    features = load_as_features(df)

    assert df[0]["usable_from"] == date(2026, 7, 4)
    assert "18:30:00+05:30" in df[0]["released_at"]
    assert features[0]["date"] == date(2026, 7, 4)
