"""Replay-derived monitoring bands for Sleeve F execution activity.

The band is registered from replay execution ledgers and is not a live
performance statistic.  Callers retain the resulting registration artifact;
they must not recompute it from live activity.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
import math
from numbers import Integral
from types import MappingProxyType
from typing import Literal

import numpy as np
import pandas as pd


BAND_CONFIG = MappingProxyType(
    {
        "schema": "meridian.activity-band.v1",
        "ledger_canonicalization": "canonical-per-session-counts-json-v1",
        "lower_percentile": 0.5,
        "minimum_windows": 100,
        "quantile_implementation": "numpy.percentile(method='linear')",
        "upper_percentile": 99.5,
    }
)
"""Frozen replay-band configuration; its quantile method is registered."""


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _hash_canonical(value: object) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


BAND_CONFIG_HASH = _hash_canonical(dict(BAND_CONFIG))


@dataclass(frozen=True)
class ActivityBand:
    """Immutable bounds registered from replay execution-count windows."""

    floor: int
    ceil: int
    window: int
    n_windows: int
    quantile_method: str
    source_ledger_hash: str
    config_hash: str


def _normalise_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise TypeError("executed counts must be non-boolean integers")
    count = int(value)
    if count < 0:
        raise ValueError("executed counts must be non-negative")
    return count


def _normalise_series(
    session_executed_counts: Mapping[date, int] | pd.Series,
) -> list[dict[str, int | str]]:
    if isinstance(session_executed_counts, pd.Series):
        items = session_executed_counts.items()
    elif isinstance(session_executed_counts, Mapping):
        items = session_executed_counts.items()
    else:
        raise TypeError("session_executed_counts must be a Mapping or pandas Series")

    rows: list[dict[str, int | str]] = []
    seen_dates: set[str] = set()
    for raw_date, raw_count in items:
        session_timestamp = pd.Timestamp(raw_date)
        if pd.isna(session_timestamp):
            raise ValueError("session dates must not be missing")
        session_date = session_timestamp.date().isoformat()
        if session_date in seen_dates:
            raise ValueError(f"duplicate session date after normalization: {session_date}")
        seen_dates.add(session_date)
        rows.append({"executed_count": _normalise_count(raw_count), "session_date": session_date})

    return sorted(rows, key=lambda row: str(row["session_date"]))


def _rolling_windows(rows: Sequence[dict[str, int | str]], window: int) -> list[int]:
    if len(rows) < window:
        return []

    counts = [int(row["executed_count"]) for row in rows]
    running_total = sum(counts[:window])
    windows = [running_total]
    for index in range(window, len(counts)):
        running_total += counts[index] - counts[index - window]
        windows.append(running_total)
    return windows


def _validate_window(window: int) -> int:
    if isinstance(window, bool) or not isinstance(window, Integral) or window <= 0:
        raise ValueError("window must be a positive integer number of complete sessions")
    return int(window)


def _derive(
    normalised_series: Sequence[list[dict[str, int | str]]], *, window: int, source_ledger_hash: str
) -> ActivityBand:
    window_list = [
        rolling_count
        for rows in normalised_series
        for rolling_count in _rolling_windows(rows, window)
    ]
    minimum_windows = int(BAND_CONFIG["minimum_windows"])
    if len(window_list) < minimum_windows:
        raise ValueError(
            "insufficient replay activity-band basis: "
            f"need at least {minimum_windows} rolling {window}-session windows, got {len(window_list)}"
        )

    lower, upper = np.percentile(
        np.asarray(window_list, dtype=np.int64),
        [BAND_CONFIG["lower_percentile"], BAND_CONFIG["upper_percentile"]],
        method="linear",
    )
    return ActivityBand(
        floor=int(math.floor(float(lower))),
        ceil=int(math.ceil(float(upper))),
        window=window,
        n_windows=len(window_list),
        quantile_method=str(BAND_CONFIG["quantile_implementation"]),
        source_ledger_hash=source_ledger_hash,
        config_hash=BAND_CONFIG_HASH,
    )


def derive_activity_band(
    session_executed_counts: Mapping[date, int] | pd.Series, *, window: int = 60
) -> ActivityBand:
    """Derive a band from every rolling ``window`` of one replay ledger.

    Fewer than ``window`` complete sessions contribute no windows.  At least
    100 windows are required for the registered 0.5th/99.5th percentiles.
    """

    window = _validate_window(window)
    rows = _normalise_series(session_executed_counts)
    return _derive([rows], window=window, source_ledger_hash=_hash_canonical(rows))


def derive_activity_band_pooled(
    count_series_list: Sequence[Mapping[date, int] | pd.Series], *, window: int = 60
) -> ActivityBand:
    """Pool replay windows across candidates/folds without crossing their seams."""

    window = _validate_window(window)
    normalised_series = [_normalise_series(series) for series in count_series_list]
    # The outer list preserves candidate/fold seams in the source-ledger hash.
    source_ledger_hash = _hash_canonical({"series": normalised_series})
    return _derive(normalised_series, window=window, source_ledger_hash=source_ledger_hash)


def band_registration_record(band: ActivityBand, window_list: Sequence[int]) -> dict[str, int | str]:
    """Return a scalar-only, hashable-by-items registration record.

    ``window_list`` must be the replay-derived list used to produce ``band``;
    its hash makes the complete registration artifact independently auditable.
    """

    normalised_windows = [_normalise_count(value) for value in window_list]
    if len(normalised_windows) != band.n_windows:
        raise ValueError(
            "window list length does not match band.n_windows: "
            f"{len(normalised_windows)} != {band.n_windows}"
        )
    record: dict[str, int | str] = {
        "ceil": band.ceil,
        "config_hash": band.config_hash,
        "floor": band.floor,
        "n_windows": band.n_windows,
        "quantile_implementation": band.quantile_method,
        "source_ledger_hash": band.source_ledger_hash,
        "window": band.window,
        "window_list_hash": _hash_canonical(normalised_windows),
    }
    record["registration_hash"] = _hash_canonical(record)
    return record


def check_activity(band: ActivityBand, rolling_60_count: int) -> Literal["ok", "investigate"]:
    """Classify one live window; callers pause and reconcile after three in a row."""

    count = _normalise_count(rolling_60_count)
    return "ok" if band.floor <= count <= band.ceil else "investigate"
