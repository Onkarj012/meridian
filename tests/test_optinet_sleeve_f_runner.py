import json

import pytest

from scripts.run_optinet_sleeve_f_full import run as run_full
from scripts.run_optinet_sleeve_f_real import SAMPLE_LIMITATION, main, run


def test_run_writes_summary_from_temp_optinet_fixture(tmp_path):
    data_root = tmp_path / "data"
    fut_root = data_root / "option_data" / "nifty_data" / "nifty_fut" / "2026" / "6"
    fut_root.mkdir(parents=True)
    older = fut_root / "nifty_fut_01_06_2026.csv"
    newer = fut_root / "nifty_fut_12_06_2026.csv"
    older.write_text(_csv(day="2026-06-01", base=23000), encoding="utf-8")
    newer.write_text(_csv(day="2026-06-12", base=23500), encoding="utf-8")
    output = tmp_path / "summary.json"

    summary = run(
        data_root=data_root,
        limit_rows=6,
        output=output,
        grid=[{"id": "sample", "target_bps": 25, "stop_bps": 25, "horizon_bars": 2, "skip_first_minutes": 0}],
    )

    written = json.loads(output.read_text(encoding="utf-8"))
    assert written == summary
    assert summary["data_root"] == str(data_root)
    assert summary["output"] == str(output)
    assert summary["rows_loaded"] == 6
    assert summary["source_files_used"] == [str(newer)]
    assert summary["normalization_quality"]["meridian_futures_validation"]["valid"] is True
    assert summary["normalization_quality"]["real_futures_validation"]["validated"] is True
    assert summary["report"]["status"] == "quarantined"
    assert summary["report"]["promoted"] is False
    assert summary["limitation"] == SAMPLE_LIMITATION
    assert summary["report"]["metrics"]["trades"] > 0
    assert "cost_scenarios" in summary["report"]["candidate"]


def test_main_accepts_repeated_grid_and_returns_summary(tmp_path):
    data_root = tmp_path / "data"
    fut_root = data_root / "option_data" / "nifty_data" / "nifty_fut" / "2026" / "6"
    fut_root.mkdir(parents=True)
    (fut_root / "nifty_fut_12_06_2026.csv").write_text(_csv(day="2026-06-12", base=23500), encoding="utf-8")
    output = tmp_path / "summary.json"

    summary = main(
        [
            "--data-root",
            str(data_root),
            "--limit-rows",
            "5",
            "--output",
            str(output),
            "--grid",
            "20,20,1",
            "--grid",
            "30,30,2",
        ]
    )

    assert output.exists()
    assert [item["id"] for item in summary["grid"]] == ["cli_grid_0", "cli_grid_1"]
    assert summary["rows_loaded"] == 5


def test_sample_runner_requires_signal_config_when_not_sample_or_fixture(tmp_path):
    data_root = tmp_path / "data"
    fut_root = data_root / "option_data" / "nifty_data" / "nifty_fut" / "2026" / "6"
    fut_root.mkdir(parents=True)
    (fut_root / "nifty_fut_12_06_2026.csv").write_text(_csv(day="2026-06-12", base=23500), encoding="utf-8")

    with pytest.raises(ValueError, match="signal_config required for non-fixture Sleeve F replay"):
        run(data_root=data_root, limit_rows=5, fixture_mode=False, sample_mode=False)


def test_full_runner_writes_grid_and_sealed_summary_from_fixture(tmp_path):
    data_root = tmp_path / "data"
    fut_root = data_root / "option_data" / "nifty_data" / "nifty_fut" / "2026" / "6"
    fut_root.mkdir(parents=True)
    for day in range(1, 6):
        (fut_root / f"nifty_fut_{day:02d}_06_2026.csv").write_text(_csv(day=f"2026-06-{day:02d}", base=23000 + day), encoding="utf-8")
    grid_path = tmp_path / "grid.json"
    grid_path.write_text(
        json.dumps(
            [
                {
                    "id": "fast",
                    "target_bps": 20,
                    "stop_bps": 15,
                    "horizon_bars": 2,
                    "skip_first_minutes": 0,
                    "signal_config": {"signal": "momentum", "direction": "long", "lookback": 1, "threshold_bps": 0},
                },
                {
                    "id": "slow",
                    "target_bps": 30,
                    "stop_bps": 20,
                    "horizon_bars": 3,
                    "skip_first_minutes": 0,
                    "signal_config": {"signal": "momentum", "direction": "long", "lookback": 1, "threshold_bps": 0},
                },
            ]
        ),
        encoding="utf-8",
    )
    output = tmp_path / "full-summary.json"

    summary = run_full(
        data_root=data_root,
        start="2026-06-01",
        end="2026-06-05",
        grid=grid_path,
        cost_bps=0,
        output=output,
    )

    written = json.loads(output.read_text(encoding="utf-8"))
    assert written == summary
    assert len(summary["config_summaries"]) == 2
    assert "outcome_counts" in summary
    assert "touch_rate" in summary
    assert "sealed_test" in summary
    assert "cost_scenarios" in summary
    assert "stress_survives_5bps" in summary
    assert summary["sealed_test"]["config_id"] == summary["report"]["candidate"]["config_id"]
    assert summary["walkforward_split"]["sealed_test_start"] == "2026-06-05"


def test_full_runner_requires_signal_config(tmp_path):
    grid_path = tmp_path / "grid.json"
    grid_path.write_text(json.dumps([{"id": "missing", "target_bps": 20, "stop_bps": 15, "horizon_bars": 2}]), encoding="utf-8")

    with pytest.raises(ValueError, match="signal_config required for non-fixture Sleeve F replay"):
        run_full(data_root=tmp_path / "data", grid=grid_path)


def _csv(*, day: str, base: int) -> str:
    rows = ["date,time,symbol,open,high,low,close,oi,volume"]
    for index in range(8):
        open_price = base + index
        high = open_price + 80
        low = open_price - 5
        close = open_price + 20
        rows.append(
            f"{day},09:{15 + index:02d}:00,NIFTY-I,{open_price},{high},{low},{close},{1000 + index},{200 + index}"
        )
    return "\n".join(rows) + "\n"
