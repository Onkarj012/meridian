# Sleeve F legacy-label parity

**Verdict: PASS**

archive_rows=449942, generated_rows=449942, value_mismatches=0, missing_or_extra_rows=0

- `parquet_path`: "/Users/onkarj012/Projects/market/intranet_optinet/models/router_v0/futures/futures_barrier_labels.parquet"
- `date_range`: {"start": "2020-01-01 09:15:00", "end": "2024-10-31 15:29:00"}
- `archive_rows`: 449942
- `generated_rows`: 449942
- `joined_rows`: 449942
- `missing_or_extra_rows`: 0
- `value_mismatch_count`: 0
- `max_abs_diff`: {"fut_close": 0.0}
- `spec_conflicts`: ["None: the protocol and task intentionally separate the archived close-touch generator from the execution-consistent generator; the archived script governs the former."]
- `semantic_distinctions`: ["Legacy labels use future closes only; execution labels use next-open entry plus OHLC first-touch.", "The archived target/stop ordering comparison is strict; execution OHLC double-touches stop first."]
