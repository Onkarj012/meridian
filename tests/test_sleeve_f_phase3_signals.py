import json
from pathlib import Path

import pytest

from features.sleeve_f_signals import build_signal_gate, enrich_rows_with_spot_basis
from scripts.run_optinet_sleeve_f_full import run as run_full


def test_opening_range_first_signal_can_only_fire_after_window() -> None:
    rows = _or_rows(
        [
            ("09:15:00", 100.0, 101.0, 99.5, 100.5, 100),
            ("09:16:00", 100.5, 101.0, 100.0, 102.0, 100),
            ("09:17:00", 100.5, 101.0, 100.0, 102.0, 100),
            ("09:18:00", 101.0, 101.5, 100.5, 102.0, 200),
        ]
    )

    signals = build_signal_gate(
        rows,
        {
            "signal": "opening_range",
            "direction": "long",
            "or_lookback_minutes": 3,
            "vol_confirm_lookback_minutes": 1,
            "vol_confirm_ratio": 1.0,
        },
    )

    assert [item["gate"] for item in signals] == [False, False, False, True]
    assert [item["or_complete"] for item in signals] == [False, False, False, True]
    assert all(item["or_high"] is None for item in signals[:3])
    assert [item["signal_value_bps"] for item in signals[:3]] == [0.0, 0.0, 0.0]
    assert signals[3]["or_high"] == pytest.approx(101.0)
    assert signals[3]["signal_value_bps"] > 0.0


def test_opening_range_short_session_guard_suppresses_all_gates() -> None:
    rows = _or_rows(
        [
            ("09:15:00", 100.0, 101.0, 99.0, 100.0, 100),
            ("09:16:00", 100.0, 101.0, 99.0, 102.0, 200),
            ("09:17:00", 100.0, 101.0, 99.0, 103.0, 200),
        ]
    )

    signals = build_signal_gate(rows, {"signal": "or_breakout", "or_lookback_minutes": 4, "vol_confirm_lookback_minutes": 1, "vol_confirm_ratio": 1.0})

    assert [item["gate"] for item in signals] == [False, False, False]
    assert all(item["or_high"] is None for item in signals)
    assert all(item["or_complete"] is False for item in signals)


def test_opening_range_volume_confirm_uses_past_median_only() -> None:
    rows = _or_rows(
        [
            ("09:15:00", 100.0, 101.0, 99.0, 100.0, 100),
            ("09:16:00", 100.0, 101.0, 99.0, 100.0, 100),
            ("09:17:00", 100.0, 101.0, 99.0, 102.0, 1000),
        ]
    )

    signals = build_signal_gate(
        rows,
        {
            "signal": "or_breakout",
            "or_lookback_minutes": 2,
            "vol_confirm_lookback_minutes": 2,
            "vol_confirm_ratio": 5.0,
        },
    )

    assert signals[2]["volume_ratio"] == pytest.approx(10.0)
    assert signals[2]["gate"] is True


def test_opening_range_gap_fade_and_gap_skip_logic() -> None:
    rows = _or_rows([("15:30:00", 100.0, 100.5, 99.5, 100.0, 100)], day="2026-01-01")
    rows.extend(
        _or_rows(
            [
                ("09:15:00", 101.0, 101.0, 100.8, 101.0, 100),
                ("09:16:00", 101.0, 101.0, 100.8, 102.0, 200),
            ],
            day="2026-01-02",
        )
    )

    fade = build_signal_gate(
        rows,
        {
            "signal": "opening_range",
            "direction": "long",
            "or_lookback_minutes": 1,
            "vol_confirm_lookback_minutes": 1,
            "vol_confirm_ratio": 1.0,
            "gap_fade": True,
        },
    )
    skip = build_signal_gate(
        rows,
        {
            "signal": "opening_range",
            "direction": "long",
            "or_lookback_minutes": 1,
            "vol_confirm_lookback_minutes": 1,
            "vol_confirm_ratio": 1.0,
            "max_gap_bps": 50,
        },
    )

    assert fade[-1]["gap_bps"] == pytest.approx(100.0)
    assert fade[-1]["gate"] is False
    assert skip[-1]["gate"] is False


def test_full_runner_threads_or_prior_close_across_day_files(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    _write_fut_file(data_root, "2026-06-01", [100.0, 101.0, 101.5])
    _write_fut_file(data_root, "2026-06-02", [103.0, 105.0, 105.5])
    blocked_grid = tmp_path / "blocked_or_gap_grid.json"
    allowed_grid = tmp_path / "allowed_or_gap_grid.json"
    _write_or_gap_grid(blocked_grid, max_gap_bps=50)
    _write_or_gap_grid(allowed_grid, max_gap_bps=200)

    blocked = run_full(data_root=data_root, start="2026-06-01", end="2026-06-02", grid=blocked_grid, cost_bps=0, output=tmp_path / "blocked.json")
    allowed = run_full(data_root=data_root, start="2026-06-01", end="2026-06-02", grid=allowed_grid, cost_bps=0, output=tmp_path / "allowed.json")

    assert blocked["sealed_test"]["metrics"]["trades"] == 0
    assert allowed["sealed_test"]["metrics"]["trades"] > 0


def test_basis_precompute_aligns_spot_dir_minutes(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    _write_spot_file(data_root, "2026-01-02", [100.0, 100.2, 100.4])
    rows = _basis_rows("2026-01-02", fut_closes=[101.0, 101.4, 101.8], oi_values=[1000, 1001, 1002])

    enriched = enrich_rows_with_spot_basis(rows, data_root=data_root)

    assert enriched[0]["spot_used"] == "dir"
    assert enriched[0]["basis_bps"] == pytest.approx(100.0)
    assert enriched[1]["basis_ret_bps"] == pytest.approx(((101.4 - 100.2) / 100.2 - (101.0 - 100.0) / 100.0) * 10000.0)


def test_basis_stale_spot_minute_suppresses_gate(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    _write_spot_file(data_root, "2026-01-02", [100.0, None, 100.0])
    rows = _basis_rows("2026-01-02", fut_closes=[100.0, 101.0, 102.0], oi_values=[1000, 1001, 1002])
    enriched = enrich_rows_with_spot_basis(rows, data_root=data_root)

    signals = build_signal_gate(enriched, {"signal": "basis_momentum", "direction": "long", "momentum_threshold_bps": 0.1})

    assert enriched[1]["spot_used"] == "stale"
    assert signals[1]["basis_bps"] is None
    assert signals[1]["gate"] is False
    assert enriched[2]["basis_ret_bps"] is None
    assert signals[2]["basis_ret_bps"] is None
    assert signals[2]["gate"] is False


def test_basis_roll_detection_is_causal_and_suppresses_from_detection_bar(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    _write_spot_file(data_root, "2026-01-02", [100.0, 100.0, 100.0, 100.0, 100.0])
    rows = _basis_rows("2026-01-02", fut_closes=[100.0, 101.0, 102.0, 103.0, 104.0], oi_values=[1000, 1001, 1002, 1400, 1401])
    enriched = enrich_rows_with_spot_basis(rows, data_root=data_root)

    signals = build_signal_gate(enriched, {"signal": "basis_momentum", "direction": "long", "momentum_threshold_bps": 0.1})
    mean_revert = build_signal_gate(enriched, {"signal": "basis_mean_revert", "direction": "short", "lookback": 3, "z_threshold": 0.1})

    assert [row["roll_day"] for row in enriched] == [False, False, False, True, True]
    assert [item["gate"] for item in signals] == [False, True, True, False, False]
    assert mean_revert[3]["z_basis"] == 0.0
    assert mean_revert[3]["gate"] is False


def test_basis_z_window_uses_current_and_past_only() -> None:
    rows = [
        {"date": "2026-01-02", "time": f"09:{15 + index:02d}:00", "symbol": "NIFTY-I", "close": 100.0, "basis_bps": basis, "volume": 100}
        for index, basis in enumerate([0.0, 0.0, 0.0, 30.0])
    ]

    signals = build_signal_gate(rows, {"signal": "basis_mean_revert", "direction": "short", "lookback": 3, "z_threshold": 1.0})

    assert [item["z_basis"] for item in signals[:3]] == [0.0, 0.0, 0.0]
    assert [item["gate"] for item in signals[:3]] == [False, False, False]
    assert signals[3]["z_basis"] > 1.0
    assert signals[3]["gate"] is True


def test_full_runner_can_run_basis_grid_with_spot_precompute(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    fut_root = data_root / "option_data" / "nifty_data" / "nifty_fut" / "2026" / "6"
    fut_root.mkdir(parents=True)
    for day in range(1, 6):
        day_key = f"2026-06-{day:02d}"
        (fut_root / f"nifty_fut_{day:02d}_06_2026.csv").write_text(_fut_csv(day_key), encoding="utf-8")
        _write_spot_file(data_root, day_key, [100.0, 100.0, 100.0, 100.0, 100.0, 100.0])
    grid_path = tmp_path / "basis_grid.json"
    grid_path.write_text(
        json.dumps(
            [
                {
                    "id": "basis_momo",
                    "target_bps": 4,
                    "stop_bps": 5,
                    "horizon_bars": 1,
                    "skip_first_minutes": 0,
                    "signal_config": {"signal": "basis_momentum", "direction": "long", "momentum_threshold_bps": 0.1, "session_start_offset": 0},
                }
            ]
        ),
        encoding="utf-8",
    )

    summary = run_full(data_root=data_root, start="2026-06-01", end="2026-06-05", grid=grid_path, cost_bps=0, output=tmp_path / "summary.json")

    assert summary["basis_spot_source"] == "auto"
    assert summary["config_summaries"][0]["config_id"] == "basis_momo"


def test_phase3_grid_file_sanity() -> None:
    grid = json.loads(Path("configs/sleeve_f_grid_phase3.json").read_text(encoding="utf-8"))
    ids = [item["id"] for item in grid]
    or_items = [item for item in grid if item["signal_config"]["signal"] in {"opening_range", "or_breakout", "or_breakdown"}]
    basis_items = [item for item in grid if item["signal_config"]["signal"] in {"basis_mean_revert", "basis_momentum"}]

    assert len(grid) == 28
    assert len(ids) == len(set(ids))
    assert len(or_items) == 16
    assert len(basis_items) == 12
    assert all(dict(item.get("signal_config") or {}).get("signal") for item in grid)
    assert all(item["skip_first_minutes"] == 15 for item in or_items)
    assert all(item["skip_first_minutes"] == 5 for item in basis_items)
    assert all(item["target_bps"] == item["stop_bps"] for item in or_items)
    mean_revert_items = [item for item in basis_items if item["signal_config"]["signal"] == "basis_mean_revert"]
    assert all(item["target_bps"] == 4 for item in mean_revert_items)
    assert all(item["stop_bps"] == 5 for item in mean_revert_items)
    assert all(item["horizon_bars"] == 5 for item in mean_revert_items)
    assert all("_t4_s5_h5_" in item["id"] for item in mean_revert_items)
    assert all("t6_s6_h10" not in item["id"] for item in mean_revert_items)


def _or_rows(values: list[tuple[str, float, float, float, float, int]], *, day: str = "2026-01-02") -> list[dict[str, object]]:
    return [
        {"date": day, "time": time, "symbol": "NIFTY-I", "open": open_, "high": high, "low": low, "close": close, "volume": volume, "oi": 1000 + index}
        for index, (time, open_, high, low, close, volume) in enumerate(values)
    ]


def _basis_rows(day: str, *, fut_closes: list[float], oi_values: list[int]) -> list[dict[str, object]]:
    return [
        {
            "date": day,
            "time": f"09:{15 + index:02d}:00",
            "symbol": "NIFTY-I",
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": 100 + index,
            "oi": oi_values[index],
        }
        for index, close in enumerate(fut_closes)
    ]


def _write_spot_file(data_root: Path, day: str, closes: list[float | None]) -> None:
    year, month, _ = day.split("-")
    spot_root = data_root / "option_data" / "nifty_data" / "nifty_spot" / year / str(int(month))
    spot_root.mkdir(parents=True, exist_ok=True)
    path = spot_root / f"nifty_spot_{day[-2:]}_{month}_{year}.csv"
    rows = ["date,time,symbol,open,high,low,close,volume"]
    for index, close in enumerate(closes):
        if close is None:
            continue
        rows.append(f"{day},09:{15 + index:02d}:00,NIFTY,{close},{close},{close},{close},0")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_fut_file(data_root: Path, day: str, closes: list[float]) -> None:
    year, month, _ = day.split("-")
    fut_root = data_root / "option_data" / "nifty_data" / "nifty_fut" / year / str(int(month))
    fut_root.mkdir(parents=True, exist_ok=True)
    path = fut_root / f"nifty_fut_{day[-2:]}_{month}_{year}.csv"
    rows = ["date,time,symbol,open,high,low,close,oi,volume"]
    for index, close in enumerate(closes):
        open_ = close
        high = close + 0.2
        low = close - 0.2
        rows.append(f"{day},09:{15 + index:02d}:00,NIFTY-I,{open_},{high},{low},{close},{1000 + index},{200 + index}")
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_or_gap_grid(path: Path, *, max_gap_bps: int) -> None:
    path.write_text(
        json.dumps(
            [
                {
                    "id": f"or_gap_{max_gap_bps}",
                    "target_bps": 1,
                    "stop_bps": 1,
                    "horizon_bars": 1,
                    "skip_first_minutes": 0,
                    "signal_config": {
                        "signal": "opening_range",
                        "direction": "long",
                        "or_lookback_minutes": 1,
                        "vol_confirm_lookback_minutes": 1,
                        "vol_confirm_ratio": 1.0,
                        "max_gap_bps": max_gap_bps,
                        "session_start_offset": 0,
                    },
                }
            ]
        ),
        encoding="utf-8",
    )


def _fut_csv(day: str) -> str:
    rows = ["date,time,symbol,open,high,low,close,oi,volume"]
    for index in range(6):
        close = 100.0 + index * 0.2
        rows.append(f"{day},09:{15 + index:02d}:00,NIFTY-I,{close},{close + 1.0},{close - 0.1},{close},{1000 + index},{200 + index}")
    return "\n".join(rows) + "\n"
