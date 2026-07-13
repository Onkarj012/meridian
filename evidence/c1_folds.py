"""Registered Sleeve F C1 expanding folds and one-sided label purging."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from evidence.walkforward import WalkForwardFold


DECISION_TIMESTAMP_COLUMN = "datetime"
LABEL_END_TIMESTAMP_COLUMN = "label_end_ts"
INNER_VALIDATION_FRACTION = 0.20


@dataclass(frozen=True)
class FoldSamples:
    """C1 train/test samples after the registered fold-boundary purge."""

    train: pd.DataFrame
    test: pd.DataFrame


def expanding_quarterly_folds() -> list[WalkForwardFold]:
    """Return the frozen 18-fold expanding quarterly 2021Q1--2025Q2 grid."""
    test_start = pd.Timestamp("2021-01-01")
    final_test_end = pd.Timestamp("2025-06-30")
    folds: list[WalkForwardFold] = []

    for index in range(18):
        oos_start = test_start + pd.DateOffset(months=3 * index)
        oos_end = oos_start + pd.offsets.QuarterEnd(0)
        folds.append(
            WalkForwardFold(
                name=f"fold_{index + 1}",
                select_start=pd.Timestamp("2020-01-01"),
                select_end=oos_start - pd.Timedelta(days=1),
                oos_start=oos_start,
                oos_end=oos_end,
            )
        )

    assert folds[-1].oos_end == final_test_end
    return folds


def purge_train_rows(
    rows: pd.DataFrame,
    boundary: pd.Timestamp | str,
    *,
    label_end_column: str = LABEL_END_TIMESTAMP_COLUMN,
) -> pd.DataFrame:
    """Drop training rows whose label windows reach ``boundary``.

    C1 has a one-sided purge: ``label_end_ts >= boundary`` is excluded only
    from the earlier, training side of a split.  The later side is untouched.
    This single primitive is used for fold, inner-validation, and threshold
    fitting boundaries.
    """
    _require_column(rows, label_end_column)
    cutoff = pd.Timestamp(boundary)
    label_end = pd.to_datetime(rows[label_end_column], errors="raise")
    return rows.loc[label_end < cutoff].copy()


def split_fold(
    rows: pd.DataFrame,
    fold: WalkForwardFold,
    *,
    decision_column: str = DECISION_TIMESTAMP_COLUMN,
    label_end_column: str = LABEL_END_TIMESTAMP_COLUMN,
) -> FoldSamples:
    """Split rows into a registered fold, purging only the train side."""
    timestamps = _decision_timestamps(rows, decision_column)
    train_end = fold.select_end + pd.Timedelta(days=1)
    test_end = fold.oos_end + pd.Timedelta(days=1)
    train = rows.loc[(timestamps >= fold.select_start) & (timestamps < train_end)]
    test = rows.loc[(timestamps >= fold.oos_start) & (timestamps < test_end)].copy()
    return FoldSamples(
        train=purge_train_rows(train, fold.oos_start, label_end_column=label_end_column),
        test=test,
    )


def inner_validation_split(
    train_rows: pd.DataFrame,
    *,
    decision_column: str = DECISION_TIMESTAMP_COLUMN,
    label_end_column: str = LABEL_END_TIMESTAMP_COLUMN,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return purged inner-train rows and the final chronological 20% validation rows."""
    timestamps = _decision_timestamps(train_rows, decision_column)
    ordered = train_rows.assign(_decision_ts=timestamps).sort_values("_decision_ts", kind="stable")
    count = len(ordered)
    if count < 2:
        raise ValueError("inner validation requires at least two training rows")

    validation_count = max(1, int(-(-count * INNER_VALIDATION_FRACTION // 1)))
    validation_start = count - validation_count
    validation = ordered.iloc[validation_start:].drop(columns="_decision_ts").copy()
    inner_train = ordered.iloc[:validation_start].drop(columns="_decision_ts")
    boundary = pd.Timestamp(validation.iloc[0][decision_column])
    return (
        purge_train_rows(inner_train, boundary, label_end_column=label_end_column),
        validation,
    )


def threshold_fitting_samples(
    rows: pd.DataFrame,
    boundary: pd.Timestamp | str,
    *,
    label_end_column: str = LABEL_END_TIMESTAMP_COLUMN,
) -> pd.DataFrame:
    """Apply the registered one-sided purge to threshold-fitting inputs."""
    return purge_train_rows(rows, boundary, label_end_column=label_end_column)


def _decision_timestamps(rows: pd.DataFrame, column: str) -> pd.Series:
    _require_column(rows, column)
    return pd.to_datetime(rows[column], errors="raise")


def _require_column(rows: pd.DataFrame, column: str) -> None:
    if column not in rows:
        raise ValueError(f"rows need {column!r} column")
