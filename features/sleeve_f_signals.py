"""Trailing-only Sleeve F signal gates for minute futures rows."""
from __future__ import annotations

import csv
import math
from collections import defaultdict, deque
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

SESSION_START_MINUTE = 9 * 60 + 15
SESSION_END_MINUTE = 15 * 60 + 30
BASIS_SIGNAL_NAMES = {"basis_mean_revert", "basis_momentum"}
OPENING_RANGE_SIGNAL_NAMES = {"opening_range", "or_breakout", "or_breakdown"}
OR_PRIOR_CLOSE_FIELD = "or_prior_close"


def momentum_n(rows: Iterable[Mapping[str, Any]], n: int) -> list[float]:
    """Return n-bar close momentum in bps, using only current/past bars."""
    lookback = _positive_window(n)
    closes: deque[float] = deque(maxlen=lookback + 1)
    output: list[float] = []
    for row in rows:
        close = _price(row.get("close", row.get("price")))
        if close is None:
            output.append(0.0)
            continue
        closes.append(close)
        if len(closes) <= lookback:
            output.append(0.0)
            continue
        base = closes[0]
        output.append((close / base - 1.0) * 10000.0 if base > 0 else 0.0)
    return output


def breakout_n(rows: Iterable[Mapping[str, Any]], n: int) -> list[bool]:
    """Return true when close exceeds the rolling max of the prior n highs."""
    lookback = _positive_window(n)
    highs: deque[float] = deque(maxlen=lookback)
    output: list[bool] = []
    for row in rows:
        close = _price(row.get("close", row.get("price")))
        output.append(close is not None and len(highs) == lookback and close > max(highs))
        high = _price(row.get("high", row.get("close", row.get("price"))))
        if high is not None:
            highs.append(high)
    return output


def breakdown_n(rows: Iterable[Mapping[str, Any]], n: int) -> list[bool]:
    """Return true when close falls below the rolling min of the prior n lows."""
    lookback = _positive_window(n)
    lows: deque[float] = deque(maxlen=lookback)
    output: list[bool] = []
    for row in rows:
        close = _price(row.get("close", row.get("price")))
        output.append(close is not None and len(lows) == lookback and close < min(lows))
        low = _price(row.get("low", row.get("close", row.get("price"))))
        if low is not None:
            lows.append(low)
    return output


def vwap_deviation(rows: Iterable[Mapping[str, Any]]) -> list[float]:
    """Return close-vs-cumulative-intraday-VWAP deviation in bps."""
    total_notional = 0.0
    total_volume = 0.0
    output: list[float] = []
    for row in rows:
        close = _price(row.get("close", row.get("price")))
        volume = _number(row.get("volume")) or 0.0
        if close is not None and volume > 0:
            total_notional += close * volume
            total_volume += volume
        if close is None or total_volume <= 0:
            output.append(0.0)
            continue
        vwap = total_notional / total_volume
        output.append((close / vwap - 1.0) * 10000.0 if vwap > 0 else 0.0)
    return output


def realized_vol_n(rows: Iterable[Mapping[str, Any]], n: int) -> list[float]:
    """Return rolling sample stddev of trailing 1-minute returns in bps."""
    window = _positive_window(n)
    returns: deque[float] = deque(maxlen=window)
    previous_close: float | None = None
    output: list[float] = []
    for row in rows:
        close = _price(row.get("close", row.get("price")))
        if close is not None and previous_close and previous_close > 0:
            returns.append((close / previous_close - 1.0) * 10000.0)
        output.append(_std(list(returns)))
        if close is not None:
            previous_close = close
    return output


def session_filter(
    rows: Iterable[Mapping[str, Any]],
    *,
    start_minute: int = SESSION_START_MINUTE,
    end_minute: int = SESSION_END_MINUTE,
    session_start_offset: int = 0,
    session_end_offset: int = 0,
) -> list[bool]:
    """Return true for bars inside the configured IST session window."""
    start = int(start_minute) + int(session_start_offset)
    end = int(end_minute) - int(session_end_offset)
    output = []
    for row in rows:
        minute = _minute_of_day(_timestamp(row))
        output.append(start <= minute <= end)
    return output


def build_signal_gate(rows: Iterable[Mapping[str, Any]], config: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Build per-bar signal values and an entry gate from a config mapping."""
    row_list = [dict(row) for row in rows]
    if not config:
        return [{"gate": True, "signal": "always", "direction": "long"} for _ in row_list]

    cfg = dict(config)
    direction = str(cfg.get("direction", "long")).lower()
    signal_name = str(cfg.get("signal", cfg.get("name", "breakout"))).lower()
    if signal_name in {"breakdown", "breakdown_n", "negative_momentum", "negative_vwap", "negative_vwap_deviation"}:
        direction = "short"
    if signal_name == "or_breakout":
        direction = "long"
    if signal_name == "or_breakdown":
        direction = "short"
    if direction not in {"long", "short"}:
        raise ValueError("Sleeve F signal direction must be 'long' or 'short'")

    grouped_indices: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, row in enumerate(row_list):
        grouped_indices[(_symbol(row), _date_key(row))].append(index)
    prior_close_by_group = _prior_close_by_group(row_list, grouped_indices)

    lookback = int(cfg.get("lookback", cfg.get("n", 30)) or 30)
    gates = [False] * len(row_list)
    values: list[dict[str, Any]] = [{"signal": signal_name, "direction": direction} for _ in row_list]

    for indices in grouped_indices.values():
        stream = [row_list[index] for index in indices]
        session = session_filter(
            stream,
            start_minute=int(cfg.get("start_minute", SESSION_START_MINUTE)),
            end_minute=int(cfg.get("end_minute", SESSION_END_MINUTE)),
            session_start_offset=int(cfg.get("session_start_offset", 0) or 0),
            session_end_offset=int(cfg.get("session_end_offset", 0) or 0),
        )
        vol = realized_vol_n(stream, int(cfg.get("realized_vol_lookback", lookback) or lookback))
        extras: list[dict[str, Any]] = [{} for _ in stream]
        if signal_name in {"breakout", "breakout_n"}:
            raw_signal = breakout_n(stream, lookback)
            signal_values = [1.0 if item else 0.0 for item in raw_signal]
        elif signal_name in {"breakdown", "breakdown_n"}:
            raw_signal = breakdown_n(stream, lookback)
            signal_values = [-1.0 if item else 0.0 for item in raw_signal]
        elif signal_name in {"momentum", "negative_momentum"}:
            signal_values = momentum_n(stream, lookback)
            threshold = abs(float(cfg.get("min_momentum_bps", cfg.get("threshold_bps", 0.0)) or 0.0))
            raw_signal = [value <= -threshold for value in signal_values] if direction == "short" else [value >= threshold for value in signal_values]
        elif signal_name in {"vwap", "vwap_deviation", "negative_vwap", "negative_vwap_deviation"}:
            signal_values = vwap_deviation(stream)
            threshold = abs(float(cfg.get("min_vwap_deviation_bps", cfg.get("threshold_bps", 0.0)) or 0.0))
            raw_signal = [value <= -threshold for value in signal_values] if direction == "short" else [value >= threshold for value in signal_values]
        elif signal_name in OPENING_RANGE_SIGNAL_NAMES:
            group_key = (_symbol(stream[0]), _date_key(stream[0])) if stream else ("", "")
            raw_signal, signal_values, extras = _opening_range_signal(
                stream,
                cfg,
                direction,
                session,
                prior_close_by_group.get(group_key),
            )
        elif signal_name in BASIS_SIGNAL_NAMES:
            raw_signal, signal_values, extras = _basis_signal(stream, cfg, direction, signal_name)
        else:
            raise ValueError(f"unsupported Sleeve F signal: {signal_name}")

        min_vol = cfg.get("min_realized_vol_bps", cfg.get("min_realized_vol"))
        min_vol_value = None if min_vol is None else float(min_vol)
        for local_index, row_index in enumerate(indices):
            vol_ok = min_vol_value is None or vol[local_index] >= min_vol_value
            gates[row_index] = bool(session[local_index] and raw_signal[local_index] and vol_ok)
            values[row_index].update(
                {
                    "gate": gates[row_index],
                    "signal_value_bps": float(signal_values[local_index]),
                    "realized_vol_bps": float(vol[local_index]),
                    "session_allowed": bool(session[local_index]),
                }
            )
            values[row_index].update(extras[local_index])

    return values


def signal_config_uses_basis(config: Mapping[str, Any] | None) -> bool:
    """Return true when a Sleeve F signal config needs spot-futures basis fields."""
    if not config:
        return False
    return str(dict(config).get("signal", dict(config).get("name", ""))).lower() in BASIS_SIGNAL_NAMES


def grid_uses_basis(grid: Iterable[Mapping[str, Any]]) -> bool:
    """Return true when any replay-grid entry needs basis precompute."""
    return any(signal_config_uses_basis(dict(item).get("signal_config")) for item in grid)


def signal_config_uses_opening_range(config: Mapping[str, Any] | None) -> bool:
    """Return true when a Sleeve F signal config needs cross-day OR state."""
    if not config:
        return False
    return str(dict(config).get("signal", dict(config).get("name", ""))).lower() in OPENING_RANGE_SIGNAL_NAMES


def grid_uses_opening_range(grid: Iterable[Mapping[str, Any]]) -> bool:
    """Return true when any replay-grid entry needs opening-range state."""
    return any(signal_config_uses_opening_range(dict(item).get("signal_config")) for item in grid)


def enrich_rows_with_prior_close(
    rows: Iterable[Mapping[str, Any]],
    prior_close_state: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Attach prior same-symbol day close while carrying state across batches."""
    row_list = [dict(row) for row in rows]
    if not row_list:
        return []
    if prior_close_state is None:
        return row_list

    output = [dict(row) for row in row_list]
    grouped_indices: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, row in enumerate(row_list):
        grouped_indices[(_symbol(row), _date_key(row))].append(index)

    by_symbol: dict[str, list[tuple[str, list[int]]]] = defaultdict(list)
    for (symbol, date_key), indices in grouped_indices.items():
        by_symbol[symbol].append((date_key, sorted(indices, key=lambda index: _timestamp(row_list[index]))))

    for symbol, groups in by_symbol.items():
        prior_close = _price(prior_close_state.get(symbol))
        for _group_date_key, indices in sorted(groups, key=lambda item: item[0]):
            for index in indices:
                output[index][OR_PRIOR_CLOSE_FIELD] = float(prior_close) if prior_close is not None else None
            last_close = None
            for index in reversed(indices):
                last_close = _price(row_list[index].get("close", row_list[index].get("price")))
                if last_close is not None:
                    break
            if last_close is not None:
                prior_close = last_close
                prior_close_state[symbol] = float(last_close)
    return output


def enrich_rows_with_spot_basis(
    rows: Iterable[Mapping[str, Any]],
    *,
    data_root: str | Path,
    spot_source: str = "auto",
    index_file_path: str | Path | None = None,
    roll_state: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Attach spot-aligned basis fields to futures rows without using future bars."""
    row_list = [dict(row) for row in rows]
    if not row_list:
        return []
    root = Path(data_root)
    index_path = Path(index_file_path) if index_file_path is not None else root / "nifty_intraday" / "NIFTY 50_minute.csv"
    output = [dict(row) for row in row_list]
    by_date: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(row_list):
        by_date[_date_key(row)].append(index)

    for date_key in sorted(by_date):
        indices = sorted(by_date[date_key], key=lambda index: _timestamp(row_list[index]))
        day_rows = [row_list[index] for index in indices]
        spot_by_minute, source_label = _load_spot_for_date(date_key, data_root=root, spot_source=spot_source, index_file_path=index_path)
        historical_median_oi_diff = _roll_history_median(roll_state)
        _update_roll_state(roll_state, day_rows)
        previous_basis: float | None = None
        previous_oi: float | None = None
        last_spot_close: float | None = None
        roll_active = False
        for row_index, row in zip(indices, day_rows):
            oi = _number(row.get("oi", row.get("open_interest")))
            roll_detected = False
            if not roll_active and oi is not None and oi > 0 and previous_oi is not None:
                roll_detected = _is_roll_oi_anomaly(previous_oi, abs(oi - previous_oi), historical_median_oi_diff)
                if roll_detected:
                    roll_active = True
                    previous_basis = None
            if oi is not None and oi > 0:
                previous_oi = oi

            minute_key = _minute_key(row)
            spot_close = spot_by_minute.get(minute_key)
            exact_spot = spot_close is not None
            if exact_spot:
                last_spot_close = spot_close
            fut_close = _price(row.get("close", row.get("price")))
            basis = (fut_close - spot_close) / spot_close * 10000.0 if exact_spot and fut_close is not None and spot_close and spot_close > 0 else None
            basis_ret = basis - previous_basis if basis is not None and previous_basis is not None else None
            if basis is not None:
                previous_basis = basis
            else:
                previous_basis = None
            output[row_index].update(
                {
                    "spot_close": float(spot_close if exact_spot else last_spot_close) if (spot_close if exact_spot else last_spot_close) is not None else None,
                    "spot_used": source_label if exact_spot else ("stale" if last_spot_close is not None else "missing"),
                    "spot_stale": not exact_spot,
                    "basis_bps": float(basis) if basis is not None else None,
                    "basis_ret_bps": float(basis_ret) if basis_ret is not None else None,
                    "roll_day": bool(roll_active),
                }
            )
    return output


def _opening_range_signal(
    rows: list[Mapping[str, Any]],
    config: Mapping[str, Any],
    direction: str,
    session: list[bool],
    prior_close: float | None,
) -> tuple[list[bool], list[float], list[dict[str, Any]]]:
    k = _positive_window(int(config.get("or_lookback_minutes", 15) or 15))
    volume_window = _positive_window(int(config.get("vol_confirm_lookback_minutes", 15) or 15))
    buffer_bps = float(config.get("entry_buffer_bps", 0.0) or 0.0)
    confirm_ratio = float(config.get("vol_confirm_ratio", 1.25) or 0.0)
    max_gap_raw = config.get("max_gap_bps")
    max_gap_bps = None if max_gap_raw is None else float(max_gap_raw)
    gap_fade = bool(config.get("gap_fade", False))
    cutoff_raw = config.get("entry_cutoff_minutes")
    entry_cutoff = None if cutoff_raw is None else int(cutoff_raw)
    raw_signal = [False] * len(rows)
    signal_values = [0.0] * len(rows)
    extras: list[dict[str, Any]] = [
        {
            "or_high": None,
            "or_low": None,
            "or_width_bps": 0.0,
            "volume_ratio": 0.0,
            "gap_bps": 0.0,
            "or_complete": False,
        }
        for _ in rows
    ]
    session_indices = [index for index, allowed in enumerate(session) if allowed]
    if len(session_indices) < k:
        return raw_signal, signal_values, extras

    opening_indices = session_indices[:k]
    opening_highs = [_price(rows[index].get("high", rows[index].get("close", rows[index].get("price")))) for index in opening_indices]
    opening_lows = [_price(rows[index].get("low", rows[index].get("close", rows[index].get("price")))) for index in opening_indices]
    if any(value is None for value in opening_highs) or any(value is None for value in opening_lows):
        return raw_signal, signal_values, extras
    or_high = max(value for value in opening_highs if value is not None)
    or_low = min(value for value in opening_lows if value is not None)
    if or_high <= 0 or or_low <= 0:
        return raw_signal, signal_values, extras
    or_width_bps = (or_high / or_low - 1.0) * 10000.0
    first_open = _price(rows[session_indices[0]].get("open", rows[session_indices[0]].get("close", rows[session_indices[0]].get("price"))))
    gap_bps = (first_open / prior_close - 1.0) * 10000.0 if first_open is not None and prior_close and prior_close > 0 else 0.0
    gap_skip = max_gap_bps is not None and abs(gap_bps) > max_gap_bps

    prior_volumes: deque[float] = deque(maxlen=volume_window)
    for session_index, row_index in enumerate(session_indices):
        row = rows[row_index]
        close = _price(row.get("close", row.get("price")))
        volume = _number(row.get("volume")) or 0.0
        volume_ratio = 0.0
        if session_index >= volume_window:
            median_volume = _median(list(prior_volumes))
            if median_volume > 0:
                volume_ratio = volume / median_volume
        if session_index < k:
            prior_volumes.append(volume)
            continue
        vol_ok = volume_ratio >= confirm_ratio
        if close is not None:
            base = or_low if direction == "short" else or_high
            signal_values[row_index] = (close / base - 1.0) * 10000.0 if base > 0 else 0.0
        entry_time_ok = entry_cutoff is None or session_index < entry_cutoff
        long_trigger = close is not None and session_index >= k and close > or_high * (1.0 + buffer_bps / 10000.0)
        short_trigger = close is not None and session_index >= k and close < or_low * (1.0 - buffer_bps / 10000.0)
        gap_direction_ok = True
        if gap_fade:
            gap_direction_ok = gap_bps < 0.0 if direction == "long" else gap_bps > 0.0
        raw_signal[row_index] = bool(
            entry_time_ok
            and not gap_skip
            and gap_direction_ok
            and vol_ok
            and (short_trigger if direction == "short" else long_trigger)
        )
        extras[row_index] = {
            "or_high": float(or_high),
            "or_low": float(or_low),
            "or_width_bps": float(or_width_bps),
            "volume_ratio": float(volume_ratio),
            "gap_bps": float(gap_bps),
            "or_complete": True,
        }
        prior_volumes.append(volume)
    return raw_signal, signal_values, extras


def _basis_signal(
    rows: list[Mapping[str, Any]],
    config: Mapping[str, Any],
    direction: str,
    signal_name: str,
) -> tuple[list[bool], list[float], list[dict[str, Any]]]:
    lookback = _positive_window(int(config.get("lookback", config.get("n", 30)) or 30))
    z_threshold = abs(float(config.get("z_threshold", config.get("z_thr", 1.5)) or 0.0))
    momentum_threshold = abs(float(config.get("momentum_threshold_bps", config.get("mom_thr_bps", 1.0)) or 0.0))
    roll_reset_window = bool(config.get("roll_reset_window", True))
    roll_flags = [bool(roll_reset_window and row.get("roll_day", False)) for row in rows]
    reset_flags = [flag and (index == 0 or not roll_flags[index - 1]) for index, flag in enumerate(roll_flags)]
    basis_values: list[float | None] = []
    valid_basis: list[bool] = []
    for row in rows:
        value = _number(row.get("basis_bps", row.get("basis")))
        spot_used = str(row.get("spot_used", "") or "").lower()
        stale = bool(row.get("spot_stale", False)) or spot_used in {"missing", "stale"}
        basis_values.append(value)
        valid_basis.append(value is not None and not stale)

    z_values, valid_counts = _basis_zscore(basis_values, valid_basis, lookback, reset_flags=reset_flags)
    basis_returns: list[float | None] = []
    previous_basis: float | None = None
    for value, valid in zip(basis_values, valid_basis):
        if value is None or not valid:
            basis_returns.append(None)
            previous_basis = None
            continue
        basis_returns.append(value - previous_basis if previous_basis is not None else None)
        previous_basis = value

    raw_signal: list[bool] = []
    signal_values = z_values if signal_name == "basis_mean_revert" else [float(value) if value is not None else 0.0 for value in basis_returns]
    for value, z_value, basis_ret, valid, count, roll_flag in zip(basis_values, z_values, basis_returns, valid_basis, valid_counts, roll_flags):
        if not valid:
            raw_signal.append(False)
            continue
        if signal_name == "basis_momentum":
            raw_signal.append(
                False
                if roll_flag or basis_ret is None
                else (basis_ret <= -momentum_threshold if direction == "short" else basis_ret >= momentum_threshold)
            )
            continue
        roll_window_ok = not roll_flag or count >= lookback
        raw_signal.append(bool(roll_window_ok and (z_value >= z_threshold if direction == "short" else z_value <= -z_threshold)))

    extras = [
        {
            "basis_bps": float(value) if value is not None else None,
            "z_basis": float(z_value),
            "basis_ret_bps": float(basis_ret) if basis_ret is not None else None,
            "spot_used": str(row.get("spot_used", "provided") or "provided"),
            "roll_day": bool(row.get("roll_day", False)),
        }
        for row, value, z_value, basis_ret in zip(rows, basis_values, z_values, basis_returns)
    ]
    return raw_signal, signal_values, extras


def _basis_zscore(
    values: list[float | None],
    valid: list[bool],
    lookback: int,
    *,
    reset_flags: list[bool] | None = None,
) -> tuple[list[float], list[int]]:
    window: deque[float] = deque(maxlen=lookback)
    z_values: list[float] = []
    counts: list[int] = []
    resets = reset_flags or [False] * len(values)
    for value, is_valid, reset in zip(values, valid, resets):
        if reset:
            window.clear()
        if value is not None and is_valid:
            window.append(value)
        counts.append(len(window))
        if value is None or not is_valid or len(window) < 2:
            z_values.append(0.0)
            continue
        sample = list(window)
        std = _std(sample)
        z_values.append((value - sum(sample) / len(sample)) / std if std > 0 else 0.0)
    return z_values, counts


def _load_spot_for_date(
    date_key: str,
    *,
    data_root: Path,
    spot_source: str,
    index_file_path: Path,
) -> tuple[dict[str, float], str]:
    source = str(spot_source or "auto").lower()
    if source not in {"auto", "spot_dir", "spot_index_file"}:
        raise ValueError("spot_source must be 'auto', 'spot_dir', or 'spot_index_file'")
    if source in {"auto", "spot_dir"}:
        rows = _load_spot_dir_for_date(date_key, data_root)
        if rows or source == "spot_dir":
            return rows, "dir" if rows else "missing"
    rows = _load_spot_index_for_date(date_key, index_file_path)
    return rows, "index_file" if rows else "missing"


def _load_spot_dir_for_date(date_key: str, data_root: Path) -> dict[str, float]:
    day = _parse_date_key(date_key)
    if day is None:
        return {}
    roots = [
        data_root / "option_data" / "nifty_data" / "nifty_spot" / f"{day.year}" / f"{day.month}",
        data_root / "option_data" / "nifty_data" / "nifty_spot" / f"{day.year}" / f"{day.month:02d}",
    ]
    path: Path | None = None
    # Archive convention is nifty_spot{DD}_{MM}_{YYYY}.csv (no separator after
    # "spot") across all years; the underscore variant is kept as a fallback.
    names = (
        f"nifty_spot{day.day:02d}_{day.month:02d}_{day.year}.csv",
        f"nifty_spot_{day.day:02d}_{day.month:02d}_{day.year}.csv",
    )
    for root in roots:
        for name in names:
            candidate = root / name
            if candidate.exists():
                path = candidate
                break
        if path is not None:
            break
    return _read_spot_csv_for_date(path, date_key) if path is not None else {}


def _load_spot_index_for_date(date_key: str, index_file_path: Path) -> dict[str, float]:
    return _read_spot_csv_for_date(index_file_path if index_file_path.exists() else None, date_key)


def _read_spot_csv_for_date(path: Path | None, date_key: str) -> dict[str, float]:
    if path is None:
        return {}
    rows: dict[str, float] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            minute_key = _minute_key(row)
            if not minute_key.startswith(f"{date_key} "):
                if rows and minute_key[:10] > date_key:
                    break
                continue
            close = _price(row.get("close", row.get("price")))
            if close is not None:
                rows[minute_key] = close
    return rows


def _is_roll_oi_anomaly(previous_oi: float, oi_diff: float, historical_median_oi_diff: float | None = None) -> bool:
    if previous_oi > 0 and oi_diff > previous_oi * 0.20:
        return True
    return bool(historical_median_oi_diff is not None and historical_median_oi_diff > 0 and oi_diff > 2.0 * historical_median_oi_diff)


def _roll_history_median(roll_state: dict[str, Any] | None) -> float | None:
    if roll_state is None:
        return None
    values = [float(value) for value in roll_state.get("oi_diff_medians", []) if _number(value) is not None]
    return _median(values[-20:]) if values else None


def _update_roll_state(roll_state: dict[str, Any] | None, rows: Iterable[Mapping[str, Any]]) -> None:
    if roll_state is None:
        return
    oi_values = [_number(row.get("oi", row.get("open_interest"))) for row in rows]
    oi_values = [value for value in oi_values if value is not None and value > 0]
    diffs = [abs(current - previous) for previous, current in zip(oi_values, oi_values[1:])]
    if not diffs:
        return
    history = roll_state.setdefault("oi_diff_medians", [])
    history.append(_median(diffs))
    del history[:-20]


def _prior_close_by_group(row_list: list[Mapping[str, Any]], grouped_indices: Mapping[tuple[str, str], list[int]]) -> dict[tuple[str, str], float | None]:
    by_symbol: dict[str, list[tuple[str, tuple[str, str], float | None, bool, float | None]]] = defaultdict(list)
    for group_key, indices in grouped_indices.items():
        ordered = sorted(indices, key=lambda index: _timestamp(row_list[index]))
        explicit_prior_present = any(OR_PRIOR_CLOSE_FIELD in row_list[index] for index in ordered)
        explicit_prior_close = None
        if explicit_prior_present:
            for index in ordered:
                explicit_prior_close = _price(row_list[index].get(OR_PRIOR_CLOSE_FIELD))
                if explicit_prior_close is not None:
                    break
        last_close = None
        for index in reversed(ordered):
            last_close = _price(row_list[index].get("close", row_list[index].get("price")))
            if last_close is not None:
                break
        by_symbol[group_key[0]].append((group_key[1], group_key, last_close, explicit_prior_present, explicit_prior_close))
    result: dict[tuple[str, str], float | None] = {}
    for groups in by_symbol.values():
        previous_close: float | None = None
        for _date, group_key, last_close, explicit_prior_present, explicit_prior_close in sorted(groups, key=lambda item: item[0]):
            result[group_key] = explicit_prior_close if explicit_prior_present else previous_close
            if last_close is not None:
                previous_close = last_close
    return result


def _positive_window(value: int) -> int:
    window = int(value)
    if window <= 0:
        raise ValueError("lookback window must be positive")
    return window


def _timestamp(row: Mapping[str, Any]) -> str:
    if row.get("timestamp") not in (None, ""):
        return str(row.get("timestamp"))
    if row.get("datetime") not in (None, ""):
        return str(row.get("datetime"))
    if row.get("date") not in (None, "") and row.get("time") not in (None, ""):
        return f"{row.get('date')}T{row.get('time')}"
    return str(row.get("date", ""))


def _date_key(row: Mapping[str, Any]) -> str:
    timestamp = _timestamp(row)
    return timestamp[:10] if len(timestamp) >= 10 else ""


def _minute_key(row: Mapping[str, Any]) -> str:
    timestamp = _timestamp(row)
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return parsed.strftime("%Y-%m-%d %H:%M:00")
    except ValueError:
        pass
    text = timestamp.replace("T", " ")
    if len(text) >= 16:
        return f"{text[:10]} {text[11:16]}:00"
    return text


def _parse_date_key(value: str) -> date | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _symbol(row: Mapping[str, Any]) -> str:
    return " ".join(str(row.get("tradingsymbol", row.get("symbol", "")) or "").upper().strip().split())


def _minute_of_day(value: Any) -> int:
    if isinstance(value, datetime):
        return value.hour * 60 + value.minute
    text = str(value or "")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.hour * 60 + parsed.minute
    except ValueError:
        pass
    if len(text) >= 16 and text[11:13].isdigit() and text[14:16].isdigit():
        return int(text[11:13]) * 60 + int(text[14:16])
    return 0


def _price(value: Any) -> float | None:
    result = _number(value)
    return result if result is not None and result > 0 else None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0
