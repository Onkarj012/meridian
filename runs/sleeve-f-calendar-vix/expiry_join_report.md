# Expiry join validation

Calendar sessions checked: 621 (2024-01-01 to 2026-07-10).

## Convention

Days to expiry is the inclusive count of supplied calendar sessions from the session through its front expiry session.

## Observed expiry weekdays

| period | first expiry | last expiry | weekdays |
| --- | --- | --- | --- |
| 2024-01 | 2024-01-25 | 2024-01-25 | Thursday |
| 2024-02 | 2024-02-29 | 2024-02-29 | Thursday |
| 2024-03 | 2024-03-28 | 2024-03-28 | Thursday |
| 2024-04 | 2024-04-25 | 2024-04-25 | Thursday |
| 2024-05 | 2024-05-30 | 2024-05-30 | Thursday |
| 2024-06 | 2024-06-27 | 2024-06-27 | Thursday |
| 2024-07 | 2024-07-25 | 2024-07-25 | Thursday |
| 2024-08 | 2024-08-29 | 2024-08-29 | Thursday |
| 2024-09 | 2024-09-26 | 2024-09-26 | Thursday |
| 2024-10 | 2024-10-31 | 2024-10-31 | Thursday |
| 2024-11 | 2024-11-28 | 2024-11-28 | Thursday |
| 2024-12 | 2024-12-26 | 2024-12-26 | Thursday |
| 2025-01 | 2025-01-30 | 2025-01-30 | Thursday |
| 2025-02 | 2025-02-27 | 2025-02-27 | Thursday |
| 2025-03 | 2025-03-27 | 2025-03-27 | Thursday |
| 2025-04 | 2025-04-24 | 2025-04-24 | Thursday |
| 2025-05 | 2025-05-29 | 2025-05-29 | Thursday |
| 2025-06 | 2025-06-26 | 2025-06-26 | Thursday |
| 2025-07 | 2025-07-31 | 2025-07-31 | Thursday |
| 2025-08 | 2025-08-28 | 2025-08-28 | Thursday |
| 2025-09 | 2025-09-25 | 2025-09-30 | Thursday, Tuesday |
| 2025-10 | 2025-10-28 | 2025-10-28 | Tuesday |
| 2025-11 | 2025-11-25 | 2025-11-25 | Tuesday |
| 2025-12 | 2025-12-30 | 2025-12-30 | Tuesday |
| 2026-01 | 2026-01-27 | 2026-01-27 | Tuesday |
| 2026-02 | 2026-02-24 | 2026-02-24 | Tuesday |
| 2026-03 | 2026-03-30 | 2026-03-30 | Monday |
| 2026-04 | 2026-04-28 | 2026-04-28 | Tuesday |
| 2026-05 | 2026-05-26 | 2026-05-26 | Tuesday |
| 2026-06 | 2026-06-30 | 2026-06-30 | Tuesday |

## Violations (8)

```json
[
  {
    "type": "expiry_not_in_expiries",
    "trade_date": "2026-07-01",
    "expiry": "2026-07-28"
  },
  {
    "type": "expiry_not_in_expiries",
    "trade_date": "2026-07-02",
    "expiry": "2026-07-28"
  },
  {
    "type": "expiry_not_in_expiries",
    "trade_date": "2026-07-03",
    "expiry": "2026-07-28"
  },
  {
    "type": "expiry_not_in_expiries",
    "trade_date": "2026-07-06",
    "expiry": "2026-07-28"
  },
  {
    "type": "expiry_not_in_expiries",
    "trade_date": "2026-07-07",
    "expiry": "2026-07-28"
  },
  {
    "type": "expiry_not_in_expiries",
    "trade_date": "2026-07-08",
    "expiry": "2026-07-28"
  },
  {
    "type": "expiry_not_in_expiries",
    "trade_date": "2026-07-09",
    "expiry": "2026-07-28"
  },
  {
    "type": "expiry_not_in_expiries",
    "trade_date": "2026-07-10",
    "expiry": "2026-07-28"
  }
]
```

## Roll checks

| expiry | day-after session | expiry day | day after |
| --- | --- | --- | --- |
| 2024-01-25 | 2024-01-29 | PASS | PASS |
| 2024-04-25 | 2024-04-26 | PASS | PASS |
| 2024-06-27 | 2024-06-28 | PASS | PASS |
| 2024-09-26 | 2024-09-27 | PASS | PASS |
| 2025-01-30 | 2025-01-31 | PASS | PASS |
| 2025-03-27 | 2025-03-28 | PASS | PASS |
| 2025-06-26 | 2025-06-27 | PASS | PASS |
| 2025-08-28 | 2025-08-29 | PASS | PASS |
| 2025-10-28 | 2025-10-29 | PASS | PASS |
| 2026-01-27 | 2026-01-28 | PASS | PASS |
| 2026-03-30 | 2026-04-01 | PASS | PASS |
| 2026-06-30 | 2026-07-01 | PASS | PASS |
