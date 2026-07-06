"""Cross-sectional stock splits with temporal embargo checks.

MERIDIAN uses StockXpert's research discipline: stocks are assigned to
train/validation/test/blind sets cross-sectionally, then every sample must stay
inside its split's allowed time window with a 10 trading-day embargo between
development periods.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd


DEFAULT_EMBARGO_DAYS = 10


@dataclass(frozen=True)
class SplitAssignment:
    name: str
    symbols: tuple[str, ...]
    start: pd.Timestamp | None = None
    end: pd.Timestamp | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbols", tuple(str(symbol).upper() for symbol in self.symbols))
        object.__setattr__(self, "start", _ts(self.start))
        object.__setattr__(self, "end", _ts(self.end))


@dataclass(frozen=True)
class CrossSectionalSplits:
    train: SplitAssignment
    val: SplitAssignment
    test: SplitAssignment
    blind: SplitAssignment
    embargo_days: int = DEFAULT_EMBARGO_DAYS

    def symbol_to_split(self) -> dict[str, str]:
        output: dict[str, str] = {}
        for assignment in (self.train, self.val, self.test, self.blind):
            for symbol in assignment.symbols:
                output[symbol] = assignment.name
        return output

    def as_dict(self) -> dict[str, object]:
        return {
            "train_symbols": list(self.train.symbols),
            "val_symbols": list(self.val.symbols),
            "test_symbols": list(self.test.symbols),
            "blind_symbols": list(self.blind.symbols),
            "embargo_days": self.embargo_days,
            "windows": {
                item.name: {
                    "start": item.start.isoformat() if item.start is not None else None,
                    "end": item.end.isoformat() if item.end is not None else None,
                }
                for item in (self.train, self.val, self.test, self.blind)
            },
        }


def make_cross_sectional_splits(
    symbols: Sequence[str] | None = None,
    *,
    metadata: Sequence[Mapping[str, Any]] | None = None,
    val_fraction: float = 0.10,
    test_fraction: float = 0.10,
    blind_fraction: float = 0.20,
    embargo_days: int = DEFAULT_EMBARGO_DAYS,
    seed: int = 42,
    train_start: str | date | None = None,
    train_end: str | date | None = None,
    val_start: str | date | None = None,
    val_end: str | date | None = None,
    test_start: str | date | None = None,
    test_end: str | date | None = None,
    blind_start: str | date | None = None,
    blind_end: str | date | None = None,
) -> CrossSectionalSplits:
    """Assign symbols to train/val/test/blind stock sets.

    If metadata includes ``sector`` and ``mcap_bucket``/``market_cap`` values,
    assignment is stratified within those buckets. Otherwise symbols are sorted
    deterministically and shuffled with ``seed``.
    """
    import random

    rows = _normalise_metadata(symbols, metadata)
    rng = random.Random(seed)
    buckets: dict[tuple[str, str], list[str]] = {}
    for row in rows:
        key = (str(row.get("sector", "UNKNOWN")), str(row.get("mcap_bucket", row.get("market_cap_bucket", "all"))))
        buckets.setdefault(key, []).append(str(row["symbol"]).upper())

    train: list[str] = []
    val: list[str] = []
    test: list[str] = []
    blind: list[str] = []
    for bucket_symbols in buckets.values():
        ordered = sorted(set(bucket_symbols))
        rng.shuffle(ordered)
        n = len(ordered)
        blind_n = _fraction_count(n, blind_fraction)
        test_n = _fraction_count(n, test_fraction)
        val_n = _fraction_count(n, val_fraction)
        blind.extend(ordered[:blind_n])
        test.extend(ordered[blind_n : blind_n + test_n])
        val.extend(ordered[blind_n + test_n : blind_n + test_n + val_n])
        train.extend(ordered[blind_n + test_n + val_n :])

    return CrossSectionalSplits(
        train=SplitAssignment("train", tuple(sorted(train)), _ts(train_start), _ts(train_end)),
        val=SplitAssignment("val", tuple(sorted(val)), _ts(val_start), _ts(val_end)),
        test=SplitAssignment("test", tuple(sorted(test)), _ts(test_start), _ts(test_end)),
        blind=SplitAssignment("blind", tuple(sorted(blind)), _ts(blind_start), _ts(blind_end)),
        embargo_days=int(embargo_days),
    )


def assert_no_leakage(samples: Iterable[Any], splits: CrossSectionalSplits | Mapping[str, Any]) -> None:
    """Fail if symbols overlap, samples appear in the wrong split, or embargo is violated."""
    split_obj = splits if isinstance(splits, CrossSectionalSplits) else _splits_from_mapping(splits)
    assignments = (split_obj.train, split_obj.val, split_obj.test, split_obj.blind)
    seen: dict[str, str] = {}
    for assignment in assignments:
        overlap = set(assignment.symbols) & set(seen)
        if overlap:
            raise AssertionError(f"symbol assigned to multiple splits: {sorted(overlap)[:5]}")
        for symbol in assignment.symbols:
            seen[symbol] = assignment.name

    _assert_embargo(split_obj)

    by_name = {assignment.name: assignment for assignment in assignments}
    symbol_to_split = split_obj.symbol_to_split()
    for sample in samples:
        row = _sample_mapping(sample)
        symbol = str(row.get("symbol", "")).upper()
        expected = symbol_to_split.get(symbol)
        if expected is None:
            raise AssertionError(f"sample symbol {symbol!r} is not assigned to any split")
        declared = row.get("split") or row.get("split_name") or row.get("fold")
        if declared is not None and str(declared) != expected:
            raise AssertionError(f"sample {symbol} declared as {declared!r}, expected {expected!r}")
        sample_date = _ts(row.get("date", row.get("timestamp")))
        assignment = by_name[expected]
        if sample_date is not None:
            if assignment.start is not None and sample_date < assignment.start:
                raise AssertionError(f"sample {symbol} at {sample_date.date()} precedes {expected} window")
            if assignment.end is not None and sample_date > assignment.end:
                raise AssertionError(f"sample {symbol} at {sample_date.date()} exceeds {expected} window")


def _assert_embargo(splits: CrossSectionalSplits) -> None:
    ordered = [splits.train, splits.val, splits.test, splits.blind]
    for left, right in zip(ordered, ordered[1:]):
        if left.end is None or right.start is None:
            continue
        required_start = left.end + pd.Timedelta(days=splits.embargo_days + 1)
        if right.start < required_start:
            raise AssertionError(
                f"{right.name} starts {right.start.date()} before {splits.embargo_days}-day embargo after {left.name}"
            )


def _normalise_metadata(
    symbols: Sequence[str] | None,
    metadata: Sequence[Mapping[str, Any]] | None,
) -> list[Mapping[str, Any]]:
    if metadata is not None:
        return [dict(item, symbol=str(item["symbol"]).upper()) for item in metadata]
    return [{"symbol": symbol} for symbol in sorted(set(str(item).upper() for item in (symbols or [])))]


def _fraction_count(total: int, fraction: float) -> int:
    if total <= 1 or fraction <= 0:
        return 0
    return min(total - 1, int(round(total * fraction)))


def _sample_mapping(sample: Any) -> Mapping[str, Any]:
    if isinstance(sample, Mapping):
        return sample
    return {
        key: getattr(sample, key)
        for key in ("symbol", "date", "timestamp", "split", "split_name", "fold")
        if hasattr(sample, key)
    }


def _splits_from_mapping(data: Mapping[str, Any]) -> CrossSectionalSplits:
    windows = data.get("windows", {}) if isinstance(data.get("windows", {}), Mapping) else {}

    def assignment(name: str, key: str) -> SplitAssignment:
        window = windows.get(name, {}) if isinstance(windows.get(name, {}), Mapping) else {}
        return SplitAssignment(
            name,
            tuple(str(item).upper() for item in data.get(key, ())),
            _ts(window.get("start")),
            _ts(window.get("end")),
        )

    return CrossSectionalSplits(
        train=assignment("train", "train_symbols"),
        val=assignment("val", "val_symbols"),
        test=assignment("test", "test_symbols"),
        blind=assignment("blind", "blind_symbols"),
        embargo_days=int(data.get("embargo_days", DEFAULT_EMBARGO_DAYS)),
    )


def _ts(value: Any) -> pd.Timestamp | None:
    if value in (None, ""):
        return None
    return pd.to_datetime(value).tz_localize(None)
