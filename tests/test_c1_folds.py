from __future__ import annotations

import pandas as pd

from evidence.c1_folds import (
    expanding_quarterly_folds,
    inner_validation_split,
    purge_train_rows,
)


def test_expanding_quarterly_folds_match_registered_date_table() -> None:
    folds = expanding_quarterly_folds()

    assert len(folds) == 18
    assert [fold.as_dict() for fold in folds] == [
        {"name": f"fold_{index}", "select_start": "2020-01-01", "select_end": train_end,
         "oos_start": test_start, "oos_end": test_end}
        for index, train_end, test_start, test_end in [
            (1, "2020-12-31", "2021-01-01", "2021-03-31"),
            (2, "2021-03-31", "2021-04-01", "2021-06-30"),
            (3, "2021-06-30", "2021-07-01", "2021-09-30"),
            (4, "2021-09-30", "2021-10-01", "2021-12-31"),
            (5, "2021-12-31", "2022-01-01", "2022-03-31"),
            (6, "2022-03-31", "2022-04-01", "2022-06-30"),
            (7, "2022-06-30", "2022-07-01", "2022-09-30"),
            (8, "2022-09-30", "2022-10-01", "2022-12-31"),
            (9, "2022-12-31", "2023-01-01", "2023-03-31"),
            (10, "2023-03-31", "2023-04-01", "2023-06-30"),
            (11, "2023-06-30", "2023-07-01", "2023-09-30"),
            (12, "2023-09-30", "2023-10-01", "2023-12-31"),
            (13, "2023-12-31", "2024-01-01", "2024-03-31"),
            (14, "2024-03-31", "2024-04-01", "2024-06-30"),
            (15, "2024-06-30", "2024-07-01", "2024-09-30"),
            (16, "2024-09-30", "2024-10-01", "2024-12-31"),
            (17, "2024-12-31", "2025-01-01", "2025-03-31"),
            (18, "2025-03-31", "2025-04-01", "2025-06-30"),
        ]
    ]


def test_purge_drops_1429_decision_when_label_crosses_boundary() -> None:
    boundary = pd.Timestamp("2021-01-01 00:00")
    rows = pd.DataFrame(
        {
            "datetime": pd.to_datetime(["2020-12-31 14:28", "2020-12-31 14:29"]),
            "label_end_ts": pd.to_datetime(["2020-12-31 15:28", "2021-01-01 00:01"]),
        }
    )

    actual = purge_train_rows(rows, boundary)

    assert actual["datetime"].tolist() == [pd.Timestamp("2020-12-31 14:28")]


def test_purge_drops_label_ending_exactly_at_boundary() -> None:
    rows = pd.DataFrame(
        {"datetime": ["2020-12-31 14:29"], "label_end_ts": ["2021-01-01 00:00"]}
    )

    assert purge_train_rows(rows, "2021-01-01").empty


def test_inner_validation_is_last_chronological_twenty_percent_and_purges_train() -> None:
    rows = pd.DataFrame(
        {
            "datetime": pd.date_range("2021-01-01 09:15", periods=10, freq="min"),
            "label_end_ts": pd.date_range("2021-01-01 09:16", periods=10, freq="min"),
        }
    )
    rows.loc[7, "label_end_ts"] = pd.Timestamp("2021-01-01 09:23")

    train, validation = inner_validation_split(rows)

    assert validation["datetime"].tolist() == [
        pd.Timestamp("2021-01-01 09:23"),
        pd.Timestamp("2021-01-01 09:24"),
    ]
    assert train["datetime"].tolist() == list(pd.date_range("2021-01-01 09:15", periods=7, freq="min"))
