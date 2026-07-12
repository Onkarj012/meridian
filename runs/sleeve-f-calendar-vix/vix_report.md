# India VIX report

Raw rows: 4291; cleaned rows: 4288; unique dates: 4288.

## Duplicate dates

### 2015-06-29 — differing rows; kept last occurrence

```json
[
  {
    "date":"2015-06-29 00:00:00",
    "open":"15.76",
    "high":"18.78",
    "low":"15.76",
    "close":"18.17",
    "volume":"0"
  },
  {
    "date":"2015-06-29 00:00:00",
    "open":"18.11",
    "high":"18.31",
    "low":"17.12",
    "close":"17.3",
    "volume":"0"
  }
]
```

### 2015-07-02 — differing rows; kept last occurrence

```json
[
  {
    "date":"2015-07-02 00:00:00",
    "open":"15.85",
    "high":"16.07",
    "low":"15.22",
    "close":"15.63",
    "volume":"0"
  },
  {
    "date":"2015-07-02 00:00:00",
    "open":"15.63",
    "high":"15.77",
    "low":"15.34",
    "close":"15.56",
    "volume":"0"
  }
]
```

### 2015-08-10 — differing rows; kept last occurrence

```json
[
  {
    "date":"2015-08-10 00:00:00",
    "open":"14.91",
    "high":"15.62",
    "low":"14.24",
    "close":"15.47",
    "volume":"0"
  },
  {
    "date":"2015-08-10 00:00:00",
    "open":"14.91",
    "high":"15.46",
    "low":"14.24",
    "close":"15.44",
    "volume":"0"
  }
]
```

## Missing dates vs trading calendar

Missing-date count: 0.

```text

```

## T-1 availability convention

A session D may use only a VIX observation dated strictly before D. The `vix_asof` helper implements the shifted/as-of join by selecting the latest clean VIX date with date < D; same-day VIX is never eligible.

| session date | VIX date used | VIX close | proof |
| --- | --- | ---: | --- |
| 2024-01-01 | 2023-12-29 | 14.5 | PASS |
| 2024-08-21 | 2024-08-20 | 13.82 | PASS |
| 2025-04-04 | 2025-04-03 | 13.6 | PASS |
| 2025-11-20 | 2025-11-19 | 11.97 | PASS |
| 2026-07-10 | 2026-07-09 | 13.36 | PASS |
