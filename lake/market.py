from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time


REQUIRED_COLUMNS = ["symbol", "timestamp", "open", "high", "low", "close", "volume"]
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


@dataclass(frozen=True)
class Candle:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


def parse_timestamp(value: str) -> datetime:
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def in_market_hours(ts: datetime) -> bool:
    return MARKET_OPEN <= ts.time() <= MARKET_CLOSE


def row_to_candle(row: dict[str, str]) -> Candle:
    normalized = normalize_row(row)
    missing = [column for column in REQUIRED_COLUMNS if column not in normalized or normalized[column] == ""]
    if missing:
        raise ValueError(f"missing required columns: {', '.join(missing)}")
    return Candle(
        symbol=normalized["symbol"].strip().upper(),
        timestamp=parse_timestamp(normalized["timestamp"]),
        open=float(normalized["open"]),
        high=float(normalized["high"]),
        low=float(normalized["low"]),
        close=float(normalized["close"]),
        volume=float(normalized["volume"]),
    )


def normalize_row(row: dict[str, str]) -> dict[str, str]:
    aliases = {
        "date": "timestamp",
        "datetime": "timestamp",
        "time": "timestamp",
    }
    normalized: dict[str, str] = {}
    for key, value in row.items():
        canonical = aliases.get(key.strip().lower(), key.strip().lower())
        normalized[canonical] = value
    return normalized


def candle_to_row(candle: Candle) -> dict[str, object]:
    return {
        "symbol": candle.symbol,
        "timestamp": candle.timestamp.isoformat(),
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
    }


def infer_symbol_from_filename(name: str) -> str:
    stem = name.rsplit(".", 1)[0]
    if stem.endswith("_minute"):
        stem = stem[: -len("_minute")]
    if stem.endswith(".NS"):
        stem = stem[: -len(".NS")]
    return stem.upper()


def floor_to_5_minutes(ts: datetime) -> datetime:
    return ts.replace(minute=ts.minute - (ts.minute % 5), second=0, microsecond=0)


def resample_to_5m(candles: list[Candle]) -> list[Candle]:
    buckets: dict[tuple[str, datetime], list[Candle]] = {}
    for candle in candles:
        buckets.setdefault((candle.symbol, floor_to_5_minutes(candle.timestamp)), []).append(candle)

    output = []
    for (symbol, ts), group in sorted(buckets.items(), key=lambda item: (item[0][0], item[0][1])):
        group.sort(key=lambda item: item.timestamp)
        output.append(
            Candle(
                symbol=symbol,
                timestamp=ts,
                open=group[0].open,
                high=max(item.high for item in group),
                low=min(item.low for item in group),
                close=group[-1].close,
                volume=sum(item.volume for item in group),
            )
        )
    return output
