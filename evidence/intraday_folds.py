"""Frozen four-fold intraday development split and sealed-test firewall."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


EMBARGO_DAYS = 7
SEALED_TEST_START = pd.Timestamp("2025-07-01")
SEALED_TEST_LAST = pd.Timestamp("2025-06-30 15:29")


@dataclass(frozen=True)
class IntradayFold:
    name: str
    train_start: str
    train_end: str
    embargo_1_start: str
    embargo_1_end: str
    calibration_start: str
    calibration_end: str
    embargo_2_start: str
    embargo_2_end: str
    oos_start: str
    oos_end: str

    def as_dict(self) -> dict[str, str]:
        return {field: str(getattr(self, field)) for field in self.__dataclass_fields__}


_FOLDS = (
    IntradayFold("fold_1", "2021-01-01", "2022-06-23", "2022-06-24", "2022-06-30", "2022-07-01", "2022-09-23", "2022-09-24", "2022-09-30", "2022-10-01", "2022-12-31"),
    IntradayFold("fold_2", "2021-01-01", "2022-12-24", "2022-12-25", "2022-12-31", "2023-01-01", "2023-03-24", "2023-03-25", "2023-03-31", "2023-04-01", "2023-06-30"),
    IntradayFold("fold_3", "2021-01-01", "2023-06-23", "2023-06-24", "2023-06-30", "2023-07-01", "2023-09-23", "2023-09-24", "2023-09-30", "2023-10-01", "2023-12-31"),
    IntradayFold("fold_4", "2021-01-01", "2023-12-24", "2023-12-25", "2023-12-31", "2024-01-01", "2024-03-24", "2024-03-25", "2024-03-31", "2024-04-01", "2024-06-30"),
)


@dataclass(frozen=True)
class FinalSplit:
    train_start: str = "2021-01-01"
    train_end: str = "2024-09-23"
    embargo_1_start: str = "2024-09-24"
    embargo_1_end: str = "2024-09-30"
    calibration_start: str = "2024-10-01"
    calibration_end: str = "2024-12-24"
    embargo_2_start: str = "2024-12-25"
    embargo_2_end: str = "2024-12-31"
    oos_start: str = "2025-01-01"
    oos_end: str = "2025-06-30"

    def as_dict(self) -> dict[str, str]:
        return {field: str(getattr(self, field)) for field in self.__dataclass_fields__}


def intraday_folds() -> list[IntradayFold]:
    """Return a copy of the frozen four-fold date table."""
    return list(_FOLDS)


def fold_table() -> list[dict[str, str]]:
    return [fold.as_dict() for fold in _FOLDS]


def final_split() -> FinalSplit:
    return FinalSplit()


def purge_rows(
    rows: pd.DataFrame,
    boundary: pd.Timestamp | str,
    horizon: int | None = None,
    *,
    label_end_column: str | None = None,
) -> pd.DataFrame:
    """Drop earlier rows whose label end reaches the boundary."""
    if "datetime" not in rows:
        raise ValueError("rows require datetime")
    cutoff = pd.Timestamp(boundary)
    timestamps = pd.to_datetime(rows["datetime"], errors="raise")
    if label_end_column is None:
        if horizon is None:
            raise ValueError("horizon or label_end_column is required")
        label_end = timestamps + pd.Timedelta(minutes=int(horizon))
    else:
        if label_end_column not in rows:
            raise ValueError(f"rows require {label_end_column!r}")
        label_end = pd.to_datetime(rows[label_end_column], errors="coerce")
    return rows.loc[label_end < cutoff].copy()


def purge_cross_boundary(rows: pd.DataFrame, boundary: pd.Timestamp | str, horizon: int) -> pd.DataFrame:
    return purge_rows(rows, boundary, horizon)


def apply_fold(rows: pd.DataFrame, fold: IntradayFold, horizon: int) -> dict[str, pd.DataFrame]:
    """Return purged train/calibration and untouched OOS rows for one fold."""
    dates = _dates(rows)
    train = rows.loc[(dates >= pd.Timestamp(fold.train_start)) & (dates <= pd.Timestamp(fold.train_end))]
    train = purge_rows(train, fold.embargo_1_start, horizon)
    calibration = rows.loc[(dates >= pd.Timestamp(fold.calibration_start)) & (dates <= pd.Timestamp(fold.calibration_end))]
    calibration = purge_rows(calibration, fold.embargo_2_start, horizon)
    oos = rows.loc[(dates >= pd.Timestamp(fold.oos_start)) & (dates <= pd.Timestamp(fold.oos_end))]
    return {"train": train, "calibration": calibration, "oos": oos}


def apply_final_split(rows: pd.DataFrame, horizon: int) -> dict[str, pd.DataFrame]:
    split = final_split()
    dates = _dates(rows)
    train = purge_rows(rows.loc[(dates >= pd.Timestamp(split.train_start)) & (dates <= pd.Timestamp(split.train_end))], split.embargo_1_start, horizon)
    calibration = purge_rows(rows.loc[(dates >= pd.Timestamp(split.calibration_start)) & (dates <= pd.Timestamp(split.calibration_end))], split.embargo_2_start, horizon)
    test = rows.loc[(dates >= pd.Timestamp(split.oos_start)) & (dates <= pd.Timestamp(split.oos_end))]
    return {"train": train, "calibration": calibration, "oos": test}


def assert_cutoff_safe(rows: pd.DataFrame, *, timestamp_column: str = "datetime") -> None:
    """Reject any data row after the sealed development cutoff."""
    if timestamp_column not in rows:
        raise ValueError(f"rows require {timestamp_column!r}")
    values = pd.to_datetime(rows[timestamp_column], errors="raise")
    if values.empty:
        raise ValueError("cutoff check requires at least one row")
    maximum = values.max()
    if maximum > SEALED_TEST_LAST:
        raise AssertionError(f"sealed-test firewall violated: max {maximum} > {SEALED_TEST_LAST}")


def sealed_test_firewall(rows: pd.DataFrame, *, timestamp_column: str = "datetime") -> pd.DataFrame:
    """Validate and return rows without exposing a post-cutoff period."""
    assert_cutoff_safe(rows, timestamp_column=timestamp_column)
    return rows.copy()


def _dates(rows: pd.DataFrame) -> pd.Series:
    if "trade_date" in rows:
        return pd.to_datetime(rows["trade_date"], errors="raise").dt.normalize()
    return pd.to_datetime(rows["datetime"], errors="raise").dt.normalize()
