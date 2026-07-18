"""Protocol-driven V1 folds, purging, and development firewall."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = REPO_ROOT / "registrations/intraday-pred-v1/evaluation_protocol.json"


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


@dataclass(frozen=True)
class FinalSplit:
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


def load_evaluation_protocol(path: str | Path = PROTOCOL_PATH) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def intraday_folds(path: str | Path = PROTOCOL_PATH) -> list[IntradayFold]:
    """Load all five development folds from the frozen protocol."""
    protocol = load_evaluation_protocol(path)
    result = []
    for item in protocol["development_folds"]:
        embargo_1_start, embargo_1_end = _date_range(item["embargoes"][0])
        embargo_2_start, embargo_2_end = _date_range(item["embargoes"][1])
        result.append(IntradayFold(
            name=f"fold_{int(item['fold'])}",
            train_start=_date_range(item["train"])[0], train_end=_date_range(item["train"])[1],
            embargo_1_start=embargo_1_start, embargo_1_end=embargo_1_end,
            calibration_start=_date_range(item["calibration"])[0], calibration_end=_date_range(item["calibration"])[1],
            embargo_2_start=embargo_2_start, embargo_2_end=embargo_2_end,
            oos_start=_date_range(item["oos"])[0], oos_end=_date_range(item["oos"])[1],
        ))
    return result


def fold_table(path: str | Path = PROTOCOL_PATH) -> list[dict[str, str]]:
    return [fold.as_dict() for fold in intraday_folds(path)]


def final_split(path: str | Path = PROTOCOL_PATH) -> FinalSplit:
    protocol = load_evaluation_protocol(path)["final_test_protocol"]
    ranges = {key: _date_range(protocol[key]) for key in ("train", "embargo_1", "calibration", "embargo_2", "sealed_test")}
    return FinalSplit(
        train_start=ranges["train"][0], train_end=ranges["train"][1],
        embargo_1_start=ranges["embargo_1"][0], embargo_1_end=ranges["embargo_1"][1],
        calibration_start=ranges["calibration"][0], calibration_end=ranges["calibration"][1],
        embargo_2_start=ranges["embargo_2"][0], embargo_2_end=ranges["embargo_2"][1],
        oos_start=ranges["sealed_test"][0], oos_end=ranges["sealed_test"][1],
    )


def purge_rows(
    rows: pd.DataFrame,
    boundary: pd.Timestamp | str,
    horizon: int | None = None,
    *,
    label_end_column: str | None = None,
) -> pd.DataFrame:
    """Keep rows whose label ends strictly before a transition boundary."""
    if "datetime" not in rows:
        raise ValueError("rows require datetime")
    if label_end_column is None and horizon is None:
        raise ValueError("horizon or label_end_column is required")
    timestamps = pd.to_datetime(rows["datetime"], errors="raise")
    if label_end_column is not None:
        if label_end_column not in rows:
            raise ValueError(f"rows require {label_end_column!r}")
        label_end = pd.to_datetime(rows[label_end_column], errors="coerce")
    else:
        label_end = timestamps + pd.Timedelta(minutes=int(horizon))
    return rows.loc[label_end < pd.Timestamp(boundary)].copy()


def apply_fold(rows: pd.DataFrame, fold: IntradayFold, horizon: int, *, protocol_path: str | Path = PROTOCOL_PATH) -> dict[str, pd.DataFrame]:
    assert_development_safe(rows, protocol_path=protocol_path)
    dates = _dates(rows)
    train = rows.loc[_between(dates, fold.train_start, fold.train_end)]
    train = purge_rows(train, fold.embargo_1_start, horizon, label_end_column=_label_end_column(train, horizon))
    calibration = rows.loc[_between(dates, fold.calibration_start, fold.calibration_end)]
    calibration = purge_rows(calibration, fold.embargo_2_start, horizon, label_end_column=_label_end_column(calibration, horizon))
    oos = rows.loc[_between(dates, fold.oos_start, fold.oos_end)]
    return {"train": train, "calibration": calibration, "oos": oos}


def apply_final_split(rows: pd.DataFrame, horizon: int, *, protocol_path: str | Path = PROTOCOL_PATH) -> dict[str, pd.DataFrame]:
    split = final_split(protocol_path)
    dates = _dates(rows)
    train = rows.loc[_between(dates, split.train_start, split.train_end)]
    train = purge_rows(train, split.embargo_1_start, horizon, label_end_column=_label_end_column(train, horizon))
    calibration = rows.loc[_between(dates, split.calibration_start, split.calibration_end)]
    calibration = purge_rows(calibration, split.embargo_2_start, horizon, label_end_column=_label_end_column(calibration, horizon))
    test = rows.loc[_between(dates, split.oos_start, split.oos_end)]
    return {"train": train, "calibration": calibration, "oos": test}


def assert_cutoff_safe(rows: pd.DataFrame, *, timestamp_column: str = "datetime", cutoff: str | pd.Timestamp | None = None, protocol_path: str | Path = PROTOCOL_PATH) -> None:
    if timestamp_column not in rows:
        raise ValueError(f"rows require {timestamp_column!r}")
    values = pd.to_datetime(rows[timestamp_column], errors="raise")
    if values.empty:
        raise ValueError("cutoff check requires at least one row")
    maximum = values.max()
    cutoff = cutoff if cutoff is not None else _date_range(load_evaluation_protocol(protocol_path)["sealed_test"])[0]
    expected = pd.Timestamp(cutoff).normalize() - pd.Timedelta(days=1) + pd.Timedelta(hours=15, minutes=29)
    if maximum > expected:
        raise AssertionError(f"sealed-test firewall violated: max {maximum} > {expected}")


def assert_development_safe(rows: pd.DataFrame, *, timestamp_column: str = "datetime", protocol_path: str | Path = PROTOCOL_PATH) -> None:
    """Reject burned 2025H1 and sealed rows before fold selection/materialization."""
    if timestamp_column not in rows:
        raise ValueError(f"rows require {timestamp_column!r}")
    protocol = load_evaluation_protocol(protocol_path)
    burned_start, burned_end = _date_range(protocol["burned_periods"][0])
    sealed_start = _date_range(protocol["sealed_test"])[0]
    values = pd.to_datetime(rows[timestamp_column], errors="raise")
    if ((values >= pd.Timestamp(burned_start)) & (values <= pd.Timestamp(burned_end) + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1))).any():
        raise AssertionError("development firewall violated: 2025H1 is burned")
    if (values >= pd.Timestamp(sealed_start)).any():
        raise AssertionError("development firewall violated: sealed-test rows are inaccessible")


def development_rows(rows: pd.DataFrame, *, timestamp_column: str = "datetime", protocol_path: str | Path = PROTOCOL_PATH) -> pd.DataFrame:
    """Return only pre-burned rows after proving no forbidden rows were supplied."""
    assert_development_safe(rows, timestamp_column=timestamp_column, protocol_path=protocol_path)
    return rows.copy()


def _label_end_column(rows: pd.DataFrame, horizon: int) -> str | None:
    for name in (f"target_h{horizon}_end_ts", f"target_h{horizon}_label_end", "label_end_ts"):
        if name in rows:
            return name
    return None


def _dates(rows: pd.DataFrame) -> pd.Series:
    if "trade_date" in rows:
        return pd.to_datetime(rows["trade_date"], errors="raise").dt.normalize()
    return pd.to_datetime(rows["datetime"], errors="raise").dt.normalize()


def _between(values: pd.Series, start: str, end: str) -> pd.Series:
    return (values >= pd.Timestamp(start)) & (values <= pd.Timestamp(end))


def _date_range(value: str) -> tuple[str, str]:
    start, end = value.split("/", 1)
    return start, end


def sealed_test_firewall(rows: pd.DataFrame, *, timestamp_column: str = "datetime", protocol_path: str | Path = PROTOCOL_PATH) -> pd.DataFrame:
    """Validate and return rows that stop before the protocol sealed test."""
    assert_cutoff_safe(rows, timestamp_column=timestamp_column, protocol_path=protocol_path)
    return rows.copy()


def purge_cross_boundary(rows: pd.DataFrame, boundary: pd.Timestamp | str, horizon: int) -> pd.DataFrame:
    return purge_rows(rows, boundary, horizon)
