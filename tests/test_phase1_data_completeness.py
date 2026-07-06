import json
from pathlib import Path

from contracts.constituents import constituent_manifest, filter_tradable_rows, load_constituent_history, tradable_on
from evidence.label_viability import audit_label_viability
from features.universe import filter_pit_universe_rows, symbol_in_pit_universe


def test_pit_constituent_boundaries_include_from_and_exclude_to(tmp_path: Path) -> None:
    source = tmp_path / "constituents.csv"
    source.write_text(
        "index_name,symbol,effective_from,effective_to,source_effective_date\n"
        "NIFTY 100,ABC,2025-01-01,2025-02-01,2025-01-03\n"
        "NIFTY 100,XYZ,2025-02-01,,2025-02-02\n",
        encoding="utf-8",
    )

    history = load_constituent_history(source)

    assert tradable_on(history, "ABC", "2025-01-01", index_name="NIFTY 100") is True
    assert tradable_on(history, "ABC", "2025-01-31T09:15:00+05:30", index_name="NIFTY 100") is True
    assert tradable_on(history, "ABC", "2025-02-01", index_name="NIFTY 100") is False
    assert symbol_in_pit_universe(history, "XYZ", "2026-01-01", index_name="NIFTY 100") is True

    manifest = constituent_manifest(history, source=source)
    json.dumps(manifest)
    assert manifest["row_count"] == 2
    assert manifest["interval_boundary"]["effective_to"] == "exclusive"


def test_filter_tradable_rows_blocks_non_members() -> None:
    history = load_constituent_history(
        [
            {"index_name": "NIFTY 100", "symbol": "ABC", "effective_from": "2025-01-01", "effective_to": "2025-02-01"},
            {"index_name": "NIFTY 100", "symbol": "XYZ", "effective_from": "2025-02-01", "effective_to": None},
        ]
    )
    trades = [
        {"timestamp": "2025-01-15", "symbol": "ABC", "signal": 1},
        {"timestamp": "2025-02-01", "symbol": "ABC", "signal": 1},
        {"timestamp": "2025-01-15", "symbol": "XYZ", "signal": 1},
        {"timestamp": "2025-02-01", "symbol": "XYZ", "signal": 1},
    ]

    filtered = filter_tradable_rows(trades, history, index_name="NIFTY 100")
    universe_filtered = filter_pit_universe_rows(trades, history, index_name="NIFTY 100")

    assert filtered == [
        {"timestamp": "2025-01-15", "symbol": "ABC", "signal": 1},
        {"timestamp": "2025-02-01", "symbol": "XYZ", "signal": 1},
    ]
    assert universe_filtered == filtered


def test_label_viability_flags_v8_style_uneconomic_geometry() -> None:
    rows = [
        {"timestamp": f"2025-01-01T09:{15 + idx:02d}:00", "symbol": "ABC", "open": 100, "high": 100.2, "low": 99.8, "close": 100}
        for idx in range(8)
    ]

    result = audit_label_viability(rows, target_bps=150, stop_bps=100, horizon_bars=3, cost_bps=41, side="long")

    assert result["observations"] == 5
    assert result["natural_hit_rate"] == 0.0
    assert result["breakeven_hit_rate"] > 0.0
    assert result["viable"] is False
    json.dumps(result["viability_table"])


def test_label_viability_passes_deliberately_viable_toy_geometry() -> None:
    rows = [
        {"timestamp": f"2025-01-01T09:{15 + idx:02d}:00", "symbol": "ABC", "open": 100 + idx * 2, "high": 101 + idx * 2, "low": 100 + idx * 2, "close": 100 + idx * 2}
        for idx in range(8)
    ]

    result = audit_label_viability(rows, target_bps=100, stop_bps=100, horizon_bars=1, cost_bps=10, side="long")

    assert result["observations"] == 7
    assert result["target_hits"] == 7
    assert result["natural_hit_rate"] == 1.0
    assert result["natural_hit_rate"] > result["breakeven_hit_rate"]
    assert result["viable"] is True


def test_same_bar_target_and_stop_resolves_to_stop() -> None:
    rows = [
        {"timestamp": "2025-01-01T09:15:00", "symbol": "ABC", "open": 100, "high": 100, "low": 100, "close": 100},
        {"timestamp": "2025-01-01T09:16:00", "symbol": "ABC", "open": 100, "high": 102, "low": 98, "close": 100},
    ]

    result = audit_label_viability(rows, target_bps=100, stop_bps=100, horizon_bars=1, cost_bps=0, side="long")

    assert result["observations"] == 1
    assert result["target_hits"] == 0
    assert result["stop_hits"] == 1
    assert result["natural_hit_rate"] == 0.0
