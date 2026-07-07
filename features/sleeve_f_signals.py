"""Trailing-only Sleeve F signal gates for minute futures rows."""
from __future__ import annotations

import math
from collections import defaultdict, deque
from datetime import datetime
from typing import Any, Iterable, Mapping

SESSION_START_MINUTE = 9 * 60 + 15
SESSION_END_MINUTE = 15 * 60 + 30


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
    if direction not in {"long", "short"}:
        raise ValueError("Sleeve F signal direction must be 'long' or 'short'")

    grouped_indices: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, row in enumerate(row_list):
        grouped_indices[(_symbol(row), _date_key(row))].append(index)

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

    return values


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
