# Meridian / OptiNet market-data lake inventory

Measured from the files under `/Users/onkarj012/Projects/market/intranet_optinet/data` on 2026-07-11. Counts below are physical files/rows unless explicitly labelled as calendar days; `.DS_Store`, backups, README, and LICENSE files are excluded from data counts. Contract comparisons use `contract_calendar.csv` or `banknifty_contract_calendar.csv`.

## Summary

| dataset | granularity | span | days/rows | source/provenance | validation state | known gaps/caveats |
|---|---|---|---:|---|---|---|
| NIFTY futures | 1-minute OHLC/OI/volume | 2020-01-01–2026-07-10 | 1,617 files / 605,188 rows | GFDL 2020-01–2024-10; Groww 2024-11–2026-06; Groww-live July | 403 passed, 10 failed, 2 Muhurat skips in the 415-row final contract validation | Missing `2024-11-01`; `2025-09-26` truncated; `2026-06-03` zero-volume/close failure; eight July files have no OI |
| NIFTY spot | 1-minute OHLC | 2020-01-01–2026-07-10 | 1,617 files / 604,323 rows | GFDL vintage through 2024-10; Groww-derived backfill thereafter; July files are present but were not written by the July spot top-up | Six source/archive overlap checks passed; archive top-up report wrote 335 files and skipped 62 | Missing `2024-11-01`, `2026-06-01`, `2026-06-04`; no OI/volume by schema |
| BankNifty futures | 1-minute OHLC/OI/volume | 2020-01-01–2026-06-30 | 1,608 files / 601,982 rows | GFDL 2020-01–2024-10; Groww 2024-11–2026-06 | 311 passed, 96 failed, 2 Muhurat skips; 93 of 404 OI checks failed in the expiry-week drift set | Missing `2024-11-01`, `2025-01-01`; `2026-06-03` zero-volume file; no July extension |
| BankNifty spot | 1-minute OHLC | 2020-01-01–2024-10-31 | 1,203 files / 449,317 rows | GFDL/vintage archive | Not part of the 2026 Groww futures backfill | No post-2024-10-31 files |
| NIFTY options | daily option-chain CSVs | 2020-01-01–2024-10-31 | 1,203 files / 2,957,950,463 bytes | Existing legacy/vintage option archive | Raw material; not revalidated here | Not touched by the 2026-07 backfill |
| BankNifty options | daily option-chain CSVs | 2020-01-01–2024-10-31 | 1,203 files / 3,944,206,614 bytes | Existing legacy/vintage option archive | Raw material; not revalidated here | Not touched by the 2026-07 backfill |
| `NIFTY 50_minute.csv` | 1-minute spot index | 2015-01-09–2026-07-10 | 1,062,380 rows / 2,845 unique dates | Mixed historical archive plus Groww top-up | Volume is zero in all 1,062,380 rows; two contract-calendar gaps | Missing `2026-06-01`, `2026-06-04`; `2026-06-17`–`2026-07-10` were appended to this source, not to spot daily archives |
| `INDIA VIX_day.csv` | daily index | 2009-03-02–2026-07-10 | 4,291 rows / 4,288 unique dates | Historical archive plus Groww daily top-up | Volume is zero in all rows; three duplicate dates | No contract-calendar use |
| `raw/udiff` | zipped daily NSE UDiFF FO bhavcopies | 2024-07-08–2026-07-10 | 496 files: 2024 121, 2025 248, 2026 127 | NSE UDiFF raw input | Source material for contract calendars | 2024 starts 2024-07-08 |
| `raw/legacy` | zipped daily legacy FO bhavcopies | 2022-01-03–2024-07-05 | 618 files: 2022 248, 2023 245, 2024 125 | Legacy NSE bhavcopy raw input | Source material for pre-UDiFF calendar construction | 2024 ends 2024-07-05 |
| `indices_minute/` | NIFTY and BANKNIFTY index minute CSVs | 2026-04-27–2026-05-25 | 2 files | Separate index archive | Sampled only | Both files have zero volume |
| `indices/` | NIFTY and BANKNIFTY daily CSVs | 2022-01-03–2026-05-27 | 2 files | Separate index archive | Sampled only | BankNifty ends 2026-05-26; NIFTY ends 2026-05-27 |
| `parquet/` | partitioned daily option parquet | 2022-01-03–2026-05-25 | 2,164 parquet files / 118,132,716 bytes | NIFTY and BANKNIFTY partitions, 1,082 files each | Sampled by filenames/metadata unavailable without pyarrow | Separate from `option_data/*_options` CSV trees |
| `market_data_cache/` | — | absent | 0 | No directory on disk | Not applicable | Requested root is absent |
| `normalized/` | — | absent | 0 | No directory on disk | Not applicable | Requested root is absent |
| `prices/` | daily equity CSVs | observed 2013-01-01–2025-01-30 | 42 files | Equity-price archive | Sampled | Not Sleeve F index/futures contract data |
| `sentiment/` | news/sentiment CSVs plus parquet | 2015-01-05–2026-06-16 across date-bearing files | 13 files (12 CSV + 1 parquet) | Mixed news, GDELT, live and daily sentiment products | Sampled | Individual files have different spans; one CSV is empty and one is a symbol list |
| `bhavcopy/` | daily CM/FO parquet | 2019-08-23–2026-06-12 | 2,339 files: CM 1,737, FO 602 | Converted bhavcopy store | Sampled by filenames | Separate from zipped `raw/` inputs |
| `nifty500/` | equity 1-minute CSVs | sampled 2015-02-02–2026-06-16 | 536 files / 22,047,139,343 bytes | Broad equity-minute archive | Sampled only, not deep-scanned | Some symbols begin later than 2015 |
| `banknifty_intraday/` | 1m/5m/15m/1h/2h/3h/1d CSVs | 2000-01-01–2026-04-22 | 7 data files / 84,383,587 bytes | Separate BankNifty intraday archive | Sampled only | Timeframe spans differ materially |
| Sleeve F contract artifacts | calendars, expiries, gaps, reports | calendars 2024-01-01–2026-07-10 / 2026-06-30 | NIFTY calendar 621 rows; BankNifty calendar 613; 31/34 expiry rows; 1/2 gap rows | Meridian-generated contract and validation artifacts | Frozen reference inputs | See `runs/sleeve-f-data-contract/` |
| `cache/router_v0` + `models/router_v0` | feature parquet + LightGBM models | cache feature rows are the frozen feature products; model folds 2021-Q1–2024-Q3 | 2 cache files / 162,926,875 bytes; 42 model artifacts (37 `.lgb`, 4 `.json`, 1 `.parquet`) | OptiNet router v0 | Frozen proxy-cache bridge is non-promotable | Frozen proxy cache SHA-256 `18ec8df4bcb928a4742f25562e6a24bd11e9838e01dc3cb31458aa72c5268ad3` |

## NIFTY futures

The archive has the exact schema `date,time,symbol,open,high,low,close,oi,volume`, with one physical file per date and no duplicate date paths. The per-year file/day counts and bar distributions are:

| year | files | bar-count distribution (`bars:files`) |
|---:|---:|---|
| 2020 | 252 | 61:1, 317:1, 318:1, 375:70, 376:179 |
| 2021 | 248 | 60:1, 221:1, 375:57, 376:189 |
| 2022 | 248 | 61:1, 374:2, 375:37, 376:208 |
| 2023 | 246 | 60:1, 334:1, 372:1, 374:1, 375:61, 376:181 |
| 2024 | 248 | 106:2, 375:38, 376:208 |
| 2025 | 248 | 61:1, 346:1, 375:40, 376:206 |
| 2026 | 127 | 375:30, 376:97 |

The normal session is 375 bars from 09:15–15:29 or 376 bars when 15:30 is present. The three extra Saturday-dated files inside the NIFTY calendar span are `2024-01-20`, `2024-03-02`, and `2024-05-18`; the sole calendar date without an archive file is `2024-11-01`, the Muhurat skip. The final validation scope reports 403 passed, 10 failed, and 2 skipped: `2025-09-26` is a 346-bar file ending 15:00; `2026-06-03` has 375 bars but zero volume and a close mismatch; `2026-07-01`, `2026-07-02`, `2026-07-03`, `2026-07-06`, `2026-07-07`, `2026-07-08`, `2026-07-09`, and `2026-07-10` have OI=0 on every row. `2025-10-21` is the second known Muhurat skip. These results agree with the final validation and forensic reports: [final_validation_report.json](../../runs/sleeve-f-data-contract/final_validation_report.json), [backfill_forensics.md](../../runs/sleeve-f-data-contract/backfill_forensics.md).

Provenance is date-bounded: GFDL through 2024-10, Groww backfill/refetch from 2024-11 through 2026-06, and a Groww-live short-lookback extension for the eight July sessions. The July endpoint returned no OI; its cumulative volume was differenced before writing, but OI is not usable. The 2025-01-01 onward Groww OI regime was normalized by ×100; the measured pre-boundary regime was 2024-11-04–2024-12-31 and the ×100 boundary was 2025-01-01. This is the OI ×100 handling described in `backfill_forensics.md`, not a new inference from the July files.

Close validation uses the volume-weighted mean of bars from 15:00 onward (NSE last-30-minute VWAP), while the final trade close is retained as a diagnostic. OI is taken from the last nonzero-OI observation. The lot table used for volume normalization is NIFTY 50 through 2024-12-31, 25 from 2025-01-01 through the 2025-09-29 session, 75 from 2025-09-30 through 2025-12-31, and 65 from 2026-01-01 onward. The 2025-01-30 expiry remains lot 25; the forensic volume residuals explicitly rule out 75 for that expiry.

## NIFTY spot and intraday source

`nifty_spot/` has the same 1,617 physical dates but the no-OI/no-volume schema `date,time,symbol,open,high,low,close`, totaling 604,323 rows. Its calendar gaps are `2024-11-01`, `2026-06-01`, and `2026-06-04`; it also contains five Saturday-dated extras: `2024-01-20`, `2024-03-02`, `2024-05-18`, `2025-02-01`, and `2026-02-01`. The row distribution is mostly 375 bars/day; the 2026 files include 17 376-bar files. The spot backfill report records six overlap checks, 335 writes, and 62 skipped archive days. The July top-up appended 6,392 rows to `NIFTY 50_minute.csv` but wrote no spot archive files; therefore July spot files found on disk are reported as present, not as outputs of that top-up ([spot_backfill_report.json](../../runs/sleeve-f-data-contract/spot_backfill_report.json), [topup_spot_2026_report.json](../../runs/sleeve-f-data-contract/topup_spot_2026_report.json)).

The per-year spot file/day counts and bar distributions are:

| year | files | bar-count distribution (`bars:files`) |
|---:|---:|---|
| 2020 | 252 | 60:1, 315:1, 317:1, 373:1, 374:3, 375:245 |
| 2021 | 248 | 103:1, 137:1, 371:1, 374:4, 375:241 |
| 2022 | 248 | 60:1, 372:1, 375:246 |
| 2023 | 246 | 60:1, 375:244, 750:1 |
| 2024 | 248 | 105:2, 375:246 |
| 2025 | 249 | 60:1, 375:248 |
| 2026 | 126 | 375:109, 376:17 |

The source `NIFTY 50_minute.csv` contains 1,062,380 rows over 2,845 unique dates, from 2015-01-09 through 2026-07-10. Volume is literally zero in every row, so this is price-only despite the volume column. Within the 621-date NIFTY contract calendar it is missing exactly `2026-06-01` and `2026-06-04`; the five Saturday extras listed above are also present. `INDIA VIX_day.csv` contains 4,291 rows over 4,288 unique dates from 2009-03-02 through 2026-07-10; its volume is zero in every row and it has three duplicate dates.

## BankNifty futures and spot

The BankNifty futures schema matches NIFTY futures and contains 1,608 files through 2026-06-30. Against the 613-row BankNifty contract calendar, the missing dates are `2024-11-01` and `2025-01-01`; the three extra Saturday files are `2024-01-20`, `2024-03-02`, and `2024-05-18`. The calendar report retains observed expiry weekdays rather than hardcoding them. The lot table is BankNifty 15 through the 2024-11 era, 30 for the 2025 regime, and 35 for 2026. The 2026 backfill report records one close failure, one volume failure, and 93 OI failures in 404 checked rows: this is the 93 expiry-week OI-drift caveat, not a claim that all BankNifty OI is unusable. `2026-06-03` is the explicit zero-volume vendor-quality anomaly. The remaining gap `2025-01-01` is recorded by `banknifty_gap_days.csv`.

BankNifty's per-year physical file counts are 252 (2020), 248 (2021), 248 (2022), 246 (2023), 248 (2024), 247 (2025), and 119 (2026). Its Groww raw OI regime was similarly identified before archive normalization: 366 x100-raw observations versus 35 near-one observations in the report's live measurements; the normalized archive still retains the 93 expiry-week residual OI failures. Volume normalization uses BankNifty lot sizes 15, 30, and 35 by the observed regime boundaries.

The BankNifty spot tree is present but vintage-only: 1,203 files, 449,317 rows, 2020-01-01–2024-10-31, with no post-2024-10-31 daily spot files. It was not extended by the 2026 backfill. The separate `banknifty_intraday/` tree is heterogeneous: 1m spans 2015-01-09–2026-04-22; 5m 2015-01-09–2024-04-09; 15m 2015-01-09–2024-04-12; 1h/2h/3h 2015-01-09–2024-04-04; and 1d 2000-01-01–2024-04-12.

## Options

`nifty_options/` and `banknifty_options/` each contain 1,203 daily CSVs spanning 2020-01-01–2024-10-31. Their measured sizes are 2,957,950,463 bytes and 3,944,206,614 bytes respectively. They were not touched by the 2026-07 backfill and were not deep-validated for this inventory. The separate `parquet/` tree has 1,082 NIFTY and 1,082 BankNifty daily option parquet files, spanning 2022-01-03–2026-05-25.

## Raw bhavcopies and auxiliary roots

The raw inputs are complementary: `raw/legacy/` has 248 files in 2022, 245 in 2023, and 125 in 2024; `raw/udiff/` has 121 in the 2024 partial year, 248 in 2025, and 127 in 2026. The converted `bhavcopy/` store has 1,737 CM parquet files from 2019-08-23–2026-06-12 and 602 FO parquet files from 2024-01-01–2026-06-12.

The small auxiliary roots are price/reference material rather than the Sleeve F contract source: `indices_minute/` has two zero-volume index files dated 2026-04-27–2026-05-25; `indices/` has two daily files from 2022-01-03 to 2026-05-27; and `prices/` has 42 equity histories observed from 2013-01-01 to 2025-01-30. `market_data_cache/` and `normalized/` do not exist. `sentiment/` has 12 CSVs plus one parquet, with date-bearing products spanning 2015-01-05–2026-06-16; the products are heterogeneous, including news, GDELT, live sentiment, an empty CSV, and a symbol list. `nifty500/` has 536 minute CSVs; a cheap sample of early, middle, and late filenames spans 2015-02-02–2026-06-16, with later-starting symbols present.

## Meridian reference data and router artifacts

The contract package contains the NIFTY calendar (621 data rows, 2024-01-01–2026-07-10), BankNifty calendar (613, 2024-01-01–2026-06-30), 31 NIFTY expiry rows, 34 BankNifty expiry rows, `gap_days.csv` with one NIFTY gap, and `banknifty_gap_days.csv` with two BankNifty gaps. The same directory contains the backfill/refetch JSON reports, `coverage_report.md`, `backfill_forensics.md`, `roll_convention.md`, and the final validation reports. The checked-in calendar checksums are in `checksums.txt` and `banknifty_checksums.txt`.

`cache/router_v0/` contains `futures_features.parquet` (131,030,676 bytes) and `futures_features_proxy.parquet` (31,896,199 bytes). `models/router_v0/` contains 42 artifacts: 37 `.lgb`, four `.json`, and one barrier-label `.parquet`. The frozen Sleeve F bridge explicitly uses the proxy cache SHA-256 `18ec8df4bcb928a4742f25562e6a24bd11e9838e01dc3cb31458aa72c5268ad3`; it is marked `promotable: false` in [bridge_report_frozen_cache.json](../../runs/sleeve-f-bridge/bridge_report_frozen_cache.json). The cache convention ends feature rows at 15:29 after warmup even though many archive sessions include 15:30; do not silently drop archive 15:30 rows when validating raw files.

## Fitness for use

Sleeve F can rely today on the NIFTY futures/spot price history and the regenerated NIFTY contract calendar for 2024-01-01–2026-06-30, with the documented VWAP-close, OI-from-last-nonzero-bar, OI ×100, lot-size, expiry-day, and Muhurat rules. The 2026-07 NIFTY futures rows are shadow-quality only: price and differenced volume exist, but OI is absent on all eight July sessions. The two NIFTY minute-source gaps (`2026-06-01`, `2026-06-04`) and the NIFTY spot archive gaps must remain explicit eligibility conditions.

BankNifty is raw material for now: futures have coverage through 2026-06-30 but the 93 expiry-week OI-drift failures and the two calendar gaps require a separate quality gate; BankNifty spot ends in 2024-10. Both options trees are raw material only, with no 2026-07 backfill. The zero-volume caveat applies to the standalone NIFTY minute and VIX sources, not to the futures archive volume fields except for the explicit `2026-06-03` anomaly.
