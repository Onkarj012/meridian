from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from lake.io import parse_simple_yaml, read_csv, write_csv


def label_features(input_path: Path, output_path: Path, config_path: Path) -> dict[str, object]:
    options = label_options(config_path)
    by_symbol: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_csv(input_path):
        by_symbol[row["symbol"]].append(row)
    labeled = [out for series in by_symbol.values() for out in label_series(series, options)]
    write_csv(output_path, labeled)
    return {"rows_written": len(labeled), "ambiguous_rows": 0, "output": str(output_path)}


def label_features_streaming(input_path: Path, output_path: Path, config_path: Path) -> dict[str, object]:
    """Label a feature file while retaining only the active symbol in memory.

    Feature files are written sorted by symbol, so a symbol boundary is sufficient
    to guarantee every label sees its complete forward horizon.
    """
    options = label_options(config_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    active_symbol: str | None = None
    active_rows: list[dict[str, str]] = []
    with input_path.open(newline="", encoding="utf-8") as source, output_path.open("w", newline="", encoding="utf-8") as destination:
        reader = csv.DictReader(source)
        writer: csv.DictWriter | None = None

        def flush() -> None:
            nonlocal writer, written, active_rows
            if not active_rows:
                return
            labeled = label_series(active_rows, options)
            if writer is None:
                writer = csv.DictWriter(destination, fieldnames=sorted({key for row in labeled for key in row}), extrasaction="ignore")
                writer.writeheader()
            writer.writerows(labeled)
            written += len(labeled)
            active_rows = []

        for row in reader:
            symbol = row["symbol"]
            if active_symbol is not None and symbol != active_symbol:
                flush()
            active_symbol = symbol
            active_rows.append(row)
        flush()
    return {"rows_written": written, "ambiguous_rows": 0, "output": str(output_path), "streaming": True}


def label_options(config_path: Path) -> dict[str, float | int]:
    config = parse_simple_yaml(config_path)
    return {
        "horizon_bars": max(1, int(config.get("horizon_minutes", 30)) // 5),
        "target_mult": float(config.get("target_atr_multiple", 0.6)),
        "stop_mult": float(config.get("stop_atr_multiple", 0.4)),
        "cost_bps": float(config.get("cost_bps", 8)),
    }


def label_series(series: list[dict[str, str]], options: dict[str, float | int]) -> list[dict[str, object]]:
    horizon_bars = int(options["horizon_bars"])
    target_mult = float(options["target_mult"])
    stop_mult = float(options["stop_mult"])
    cost_bps = float(options["cost_bps"])
    labeled: list[dict[str, object]] = []
    for index, row in enumerate(series):
        close = float(row["close"])
        future = series[index + 1 : index + 1 + horizon_bars]
        if close <= 0 or len(future) < horizon_bars:
            labeled.append(excluded_label(row, len(future)))
            continue
        atr = max(float(row.get("atr") or 0), close * 0.001)
        long_outcome = simulate_exit(close, atr, future, target_mult, stop_mult, "long")
        short_outcome = simulate_exit(close, atr, future, target_mult, stop_mult, "short")
        long_net = float(long_outcome["gross_return_bps"]) - cost_bps
        short_net = float(short_outcome["gross_return_bps"]) - cost_bps
        target_class = "LONG" if long_outcome["label"] == "LONG_SUCCESS" else "SHORT" if short_outcome["label"] == "SHORT_SUCCESS" else "NO_TRADE"
        out = dict(row)
        out.update(
            {
                "target_class": target_class, "long_label": long_outcome["label"], "short_label": short_outcome["label"],
                "exit_reason": long_outcome["exit_reason"], "long_exit_reason": long_outcome["exit_reason"], "short_exit_reason": short_outcome["exit_reason"],
                "gross_return_bps": long_outcome["gross_return_bps"], "net_return_bps": long_net,
                "mfe_bps": long_outcome["mfe_bps"], "mae_bps": long_outcome["mae_bps"],
                "risk_reward_ratio": target_mult / stop_mult if stop_mult else 0.0,
                "long_return_bps": long_outcome["gross_return_bps"], "short_return_bps": short_outcome["gross_return_bps"],
                "long_gross_return_bps": long_outcome["gross_return_bps"], "short_gross_return_bps": short_outcome["gross_return_bps"],
                "long_net_return_bps": long_net, "short_net_return_bps": short_net,
                "long_mfe_bps": long_outcome["mfe_bps"], "long_mae_bps": long_outcome["mae_bps"],
                "short_mfe_bps": short_outcome["mfe_bps"], "short_mae_bps": short_outcome["mae_bps"],
                "long_exit_bars": long_outcome["exit_bars"], "short_exit_bars": short_outcome["exit_bars"],
                "ambiguous_bar": "false", "label_excluded": "false",
            }
        )
        labeled.append(out)
    return labeled


def simulate_exit(close: float, atr: float, future: list[dict[str, str]], target_mult: float, stop_mult: float, side: str) -> dict[str, object]:
    target = close + target_mult * atr if side == "long" else close - target_mult * atr
    stop = close - stop_mult * atr if side == "long" else close + stop_mult * atr
    mfe_bps = 0.0
    mae_bps = 0.0
    for offset, future_row in enumerate(future, start=1):
        high, low = float(future_row["high"]), float(future_row["low"])
        if side == "long":
            mfe_bps, mae_bps = max(mfe_bps, (high - close) / close * 10000), min(mae_bps, (low - close) / close * 10000)
            hit_target, hit_stop = high >= target, low <= stop
        else:
            mfe_bps, mae_bps = max(mfe_bps, (close - low) / close * 10000), min(mae_bps, (close - high) / close * 10000)
            hit_target, hit_stop = low <= target, high >= stop
        # OHLC cannot establish which level was hit first. Stop-first is conservative.
        if hit_stop:
            gross = (stop - close) / close * 10000 if side == "long" else (close - stop) / close * 10000
            return outcome(side, "stop", gross, offset, mfe_bps, mae_bps)
        if hit_target:
            gross = (target - close) / close * 10000 if side == "long" else (close - target) / close * 10000
            return outcome(side, "target", gross, offset, mfe_bps, mae_bps)
    horizon_close = float(future[-1]["close"])
    gross = (horizon_close - close) / close * 10000 if side == "long" else (close - horizon_close) / close * 10000
    return outcome(side, "horizon", gross, len(future), mfe_bps, mae_bps)


def outcome(side: str, exit_reason: str, gross_return_bps: float, exit_bars: int, mfe_bps: float, mae_bps: float) -> dict[str, object]:
    label = f"{side.upper()}_SUCCESS" if exit_reason == "target" else f"{side.upper()}_FAIL" if exit_reason == "stop" else "NO_TRADE"
    return {"label": label, "exit_reason": exit_reason, "gross_return_bps": gross_return_bps, "exit_bars": exit_bars, "mfe_bps": mfe_bps, "mae_bps": mae_bps}


def excluded_label(row: dict[str, str], future_rows: int) -> dict[str, object]:
    out = dict(row)
    out.update(
        {
            "target_class": "NO_TRADE", "long_label": "NO_TRADE", "short_label": "NO_TRADE",
            "long_return_bps": 0.0, "short_return_bps": 0.0,
            "long_gross_return_bps": 0.0, "short_gross_return_bps": 0.0,
            "long_net_return_bps": 0.0, "short_net_return_bps": 0.0,
            "gross_return_bps": 0.0, "net_return_bps": 0.0, "mfe_bps": 0.0, "mae_bps": 0.0, "risk_reward_ratio": 0.0,
            "long_mfe_bps": 0.0, "long_mae_bps": 0.0, "short_mfe_bps": 0.0, "short_mae_bps": 0.0,
            "long_exit_bars": future_rows, "short_exit_bars": future_rows,
            "exit_reason": "no_future", "long_exit_reason": "no_future", "short_exit_reason": "no_future",
            "ambiguous_bar": "false", "label_excluded": "true",
        }
    )
    return out
