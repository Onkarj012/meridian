import json
from pathlib import Path

import duckdb

from contracts.snapshot import build_source_snapshot
from evidence.backtest import FULL_COST_BPS, run_backtest
from evidence.gates import DEFAULT_PROMOTION_THRESHOLDS, build_promotion_readiness_report
from lake.bronze import build_snapshot_lake, register_lake


HEADER = "timestamp,open,high,low,close,volume\n"


def _minute(rows: str) -> str:
    return HEADER + rows


def test_source_snapshot_selects_contracted_feeds_and_renames_format(tmp_path: Path) -> None:
    root = tmp_path / "source"
    equities = root / "nifty500"
    indices = root / "indices_minute"
    equities.mkdir(parents=True)
    indices.mkdir()
    (equities / "ABC_minute.csv").write_text(_minute("2025-01-01T09:15:00+05:30,1,2,1,2,10\n"), encoding="utf-8")
    (indices / "NIFTY_minute.csv").write_text(_minute("2025-01-01T09:15:00+05:30,1,2,1,2,10\n"), encoding="utf-8")
    (indices / "MIDCPNIFTY_minute.csv").write_text(_minute("2025-01-01T09:15:00+05:30,1,2,1,2,10\n"), encoding="utf-8")

    result = build_source_snapshot(root, tmp_path / "contracts")
    snapshot = json.loads(Path(result["snapshot"]).read_text(encoding="utf-8"))

    assert snapshot["format"] == "meridian.market-source-snapshot.v1"
    assert result["selected_source_count"] == 2
    assert {source["path"] for source in snapshot["sources"] if source["selection_decision"].startswith("selected")} == {
        "indices_minute/NIFTY_minute.csv",
        "nifty500/ABC_minute.csv",
    }


def test_snapshot_lake_writes_queryable_bronze_and_silver(tmp_path: Path) -> None:
    source = tmp_path / "source"
    equities = source / "nifty500"
    equities.mkdir(parents=True)
    (equities / "ABC_minute.csv").write_text(
        _minute(
            "2025-01-02 09:15:00,100,101,99,100,10\n"
            "2025-01-02 09:16:00,100,102,99,101,11\n"
            "2025-01-02 09:16:00,101,102,100,101,12\n"
            "2025-01-02 16:00:00,100,101,99,100,14\n"
        ),
        encoding="utf-8",
    )
    snapshot = Path(build_source_snapshot(source, tmp_path / "contracts")["snapshot"])
    lake = tmp_path / "lake"

    result = build_snapshot_lake(snapshot, source, lake)
    assert result.rows == 2
    assert result.quarantined_rows == 2

    catalog = register_lake(lake)
    connection = duckdb.connect(str(catalog), read_only=True)
    try:
        assert connection.execute("select count(*) from market_bronze").fetchone()[0] == 4
        assert connection.execute("select count(*) from market_silver").fetchone()[0] == 2
    finally:
        connection.close()


def test_promotion_thresholds_match_design_doc_defaults_and_gate_candidate(tmp_path: Path) -> None:
    assert DEFAULT_PROMOTION_THRESHOLDS.min_trades == 300
    assert DEFAULT_PROMOTION_THRESHOLDS.min_trading_days == 100
    assert DEFAULT_PROMOTION_THRESHOLDS.max_single_symbol_share == 0.20
    assert DEFAULT_PROMOTION_THRESHOLDS.min_distinct_symbols == 20
    assert DEFAULT_PROMOTION_THRESHOLDS.min_positive_fold_share == 0.70
    assert DEFAULT_PROMOTION_THRESHOLDS.min_dsr == 0.0

    validation = tmp_path / "validation.json"
    validation.write_text(
        json.dumps(
            {
                "candidates": [
                    {
                        "model_family": "momentum",
                        "robust_validation_eligible": True,
                        "trades": 300,
                        "trading_days": 100,
                        "ci_low_bps": 1.0,
                        "max_symbol_trade_share": 0.20,
                        "distinct_symbols": 20,
                        "side_results": {"long": {"count": 300, "distinct_symbols": 20}},
                        "folds": [{"expected_value_bps": 1.0} for _ in range(7)] + [{"expected_value_bps": 0.0} for _ in range(3)],
                        "net_sharpe": 1.1,
                        "baselines": {"random": 0.0, "momentum": 0.9},
                        "deflated_sharpe_ratio": 0.1,
                        "sealed_test_passed": True,
                        "cost_scenarios": {"full_cost": {"expectancy_ci95_low_bps": 1.0, "profit_factor": 1.1}},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = build_promotion_readiness_report(tmp_path / "promotion.json", validation_report=validation)
    saved = json.loads((tmp_path / "promotion.json").read_text(encoding="utf-8"))
    assert result["sealed_test_decision"] == "eligible_to_spend_2025_once"
    assert saved["acceptance"]["eligible"] is True


def test_backtest_uses_one_full_cost_mode_ignoring_light_cost_config(tmp_path: Path) -> None:
    data = tmp_path / "gold.csv"
    data.write_text(
        "timestamp,symbol,confidence,long_label,label_excluded\n"
        "2025-01-01T09:15:00+05:30,ABC,0.9,LONG_SUCCESS,false\n",
        encoding="utf-8",
    )
    config = tmp_path / "config.yml"
    config.write_text("top_k: 1\nmin_confidence: 0.5\ncost_bps: 0\nslippage_bps: 0\n", encoding="utf-8")

    report = run_backtest(data, tmp_path / "report.md", config)
    assert report["trades"] == 1
    assert report["net_score_after_costs"] == 1.0 - FULL_COST_BPS / 10000
