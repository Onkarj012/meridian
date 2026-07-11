# Groww backfill forensics

Generated offline by `backfill_forensics.py` from the archive, calendar, saved report, frozen cache, and local UDiFF ZIPs.

## Baseline failure inventory

The supplied report contains 324 failure entries. Its category counts at inspection time were:

| category | days |
| --- | --- |
| oi | 267 |
| close+oi | 25 |
| volume+oi | 20 |
| close | 4 |
| volume | 3 |
| close+volume+oi | 2 |
| missing_file | 1 |
| bar_count+volume+oi | 1 |
| bar_count+close+oi | 1 |

Reconstruction over the 347 gap dates finds 346 files and 1 missing path. There are 32 total close failures in the baseline report; after excluding the two close+volume overlaps and the known Muhurat day 2025-10-21, the requested close-failure category contains 29 days.

## 1a. OI trailing-bar hypothesis

There are 316 baseline OI-failing files. A deterministic sample of up to 10 per year (29 rows) is shown; `trailing_zero_oi_times` lists the contiguous zero-OI suffix from oldest to newest.

| date | bars | last | trailing_zero_oi_times | last_nonzero_oi_time | last_nonzero_oi | bhavcopy_oi | relative_error | passes_5pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2024-11-22 | 376.0 | 15:30:00 | none | 15:30:00 | 10549400.0 | 10009675.0 | 0.05392033207871384 | False |
| 2024-11-25 | 376.0 | 15:30:00 | none | 15:30:00 | 10321025.0 | 9327400.0 | 0.10652754250916655 | False |
| 2024-11-26 | 376.0 | 15:30:00 | none | 15:30:00 | 9338925.0 | 7996775.0 | 0.16783640905239924 | False |
| 2024-11-27 | 376.0 | 15:30:00 | none | 15:30:00 | 8845350.0 | 6214400.0 | 0.4233634783728115 | False |
| 2024-11-28 | 375.0 | 15:29:00 | none | 15:29:00 | 6638275.0 | 3354450.0 | 0.9789458778637332 | False |
| 2024-12-20 | 376.0 | 15:30:00 | none | 15:30:00 | 11041150.0 | 10499150.0 | 0.051623226642156746 | False |
| 2024-12-23 | 376.0 | 15:30:00 | none | 15:30:00 | 10006375.0 | 9217925.0 | 0.08553443426801585 | False |
| 2024-12-24 | 376.0 | 15:30:00 | none | 15:30:00 | 9202575.0 | 5721775.0 | 0.6083426908607906 | False |
| 2024-12-26 | 376.0 | 15:30:00 | none | 15:30:00 | 5561475.0 | 3477450.0 | 0.5992968985894837 | False |
| 2025-01-01 | 376.0 | 15:30:00 | 15:29:00, 15:30:00 | 15:28:00 | 133488.0 | 13270450.0 | -0.9899409590481106 | False |
| 2025-02-07 | 376.0 | 15:30:00 | 15:29:00, 15:30:00 | 15:28:00 | 169874.0 | 16761000.0 | -0.9898649245271762 | False |
| 2025-03-20 | 375.0 | 15:29:00 | 15:29:00 | 15:28:00 | 161178.0 | 15414525.0 | -0.9895437582410097 | False |
| 2025-05-06 | 376.0 | 15:30:00 | 15:29:00, 15:30:00 | 15:28:00 | 136041.0 | 13527900.0 | -0.9899436719668241 | False |
| 2025-06-12 | 376.0 | 15:30:00 | 15:29:00, 15:30:00 | 15:28:00 | 122287.0 | 11388075.0 | -0.9892618374922891 | False |
| 2025-07-22 | 376.0 | 15:30:00 | 15:29:00, 15:30:00 | 15:28:00 | 134010.0 | 13220850.0 | -0.9898637379593597 | False |
| 2025-09-01 | 376.0 | 15:30:00 | 15:29:00, 15:30:00 | 15:28:00 | 167967.0 | 16621050.0 | -0.9898943207559089 | False |
| 2025-10-10 | 375.0 | 15:29:00 | none | 15:29:00 | 175444.0 | 17289075.0 | -0.9898523200344727 | False |
| 2025-11-20 | 376.0 | 15:30:00 | 15:30:00 | 15:29:00 | 166855.0 | 14612850.0 | -0.9885816250765593 | False |
| 2025-12-31 | 376.0 | 15:30:00 | 15:30:00 | 15:29:00 | 141319.0 | 14026480.0 | -0.9899248421556941 | False |
| 2026-01-01 | 376.0 | 15:30:00 | 15:30:00 | 15:29:00 | 140567.0 | 14003665.0 | -0.9899621277715512 | False |
| 2026-01-09 | 375.0 | 15:29:00 | none | 15:29:00 | 170866.0 | 16876470.0 | -0.9898754893647783 | False |
| 2026-01-20 | 376.0 | 15:30:00 | 15:30:00 | 15:29:00 | 169965.0 | 16492255.0 | -0.9896942534541213 | False |
| 2026-01-30 | 376.0 | 15:30:00 | 15:30:00 | 15:29:00 | 184661.0 | 18228535.0 | -0.9898696741125933 | False |
| 2026-02-09 | 375.0 | 15:29:00 | none | 15:29:00 | 153758.0 | 15276105.0 | -0.9899347379453074 | False |
| 2026-02-18 | 376.0 | 15:30:00 | 15:30:00 | 15:29:00 | 157474.0 | 15080845.0 | -0.9895580121670902 | False |
| 2026-02-26 | 376.0 | 15:30:00 | 15:30:00 | 15:29:00 | 131514.0 | 13096200.0 | -0.9899578503688092 | False |
| 2026-03-10 | 376.0 | 15:30:00 | 15:30:00 | 15:29:00 | 179223.0 | 17367675.0 | -0.9896806567373008 | False |
| 2026-03-18 | 375.0 | 15:29:00 | none | 15:29:00 | 182917.0 | 17452955.0 | -0.9895194252205429 | False |
| 2026-03-30 | 376.0 | 15:30:00 | 15:30:00 | 15:29:00 | 75877.0 | 6638320.0 | -0.9885698489979392 | False |

Across all OI failures, the last nonzero-OI observation passes the ±5% check on 0/316 days. The 15:30-zero pattern is real for the 2025/2026 sample, but it is not the whole explanation: 2024 has nonzero last observations that still miss, and Groww's 2025–2026 nonzero OI is approximately 1/100 of the bhavcopy OI (for example 133,488 vs 13,270,450 on 2025-01-01). The fallback is therefore diagnostic and explicit; it does not make a bad OI value pass.

## 1e. OI units boundary scan

The scan covers all 346 explicit Groww target dates (`gap_days.csv` minus
`2024-11-01`) before archive normalization. The ratio is `bhavcopy front_oi /
file last nonzero OI`.

| regime | dates | date range | ratio min | ratio median | ratio max | core-cluster count |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| normal units | 39 | 2024-11-04–2024-12-31 | 0.505320 | 0.985870 | 0.995242 | 35 at 0.8–1.25 |
| Groww ×100-underreported | 307 | 2025-01-01–2026-03-30 | 33.737680 | 97.262231 | 99.955857 | 277 at 80–125 |

The exact boundary is **2025-01-01**: 2024-12-31 is 0.985031 and 2025-01-01
is 99.413056. There is no boundary ambiguity: the largest pre-boundary ratio
is 0.995242 and the smallest post-boundary ratio is 33.737680. The 30
post-boundary observations below 80 and four late-expiry 2024 observations
below 0.8 are intraday/expiry OI drift, not competing unit regimes.

| date | file last nonzero OI | bhavcopy front OI | ratio |
| --- | ---: | ---: | ---: |
| 2024-12-30 | 12,432,650 | 12,373,500 | 0.995242 |
| 2024-12-31 | 13,493,175 | 13,291,200 | 0.985031 |
| 2025-01-01 | 133,488 | 13,270,450 | 99.413056 |
| 2025-01-02 | 126,527 | 12,424,650 | 98.197618 |
| 2025-09-26 | 129,966 | 9,691,950 | 74.572965 |
| 2025-09-29 | 181,713 | 6,130,575 | 33.737680 |

The archive migration multiplied OI by 100 for exactly **307** target files,
from 2025-01-01 onward. A second run classified all 307 as already normalized
and changed no files. Spot checks after migration: 2025-01-01 ratio 0.994131,
2025-06-10 ratio 0.971114, and 2026-02-10 ratio 0.986249.

## 1b. Vintage and frozen-cache convention

| vintage | files | bar counts | last-time counts | files with 15:30 | last OI zero |
| --- | --- | --- | --- | --- | --- |
| 2023 | 246 | {60: np.int64(1), 334: np.int64(1), 372: np.int64(1), 374: np.int64(1), 375: np.int64(61), 376: np.int64(181)} | {'15:30:00': np.int64(184), '15:29:00': np.int64(61), '19:14:00': np.int64(1)} | 184 | 1 |
| 2024 Jan-Oct | 209 | {106: np.int64(2), 375: np.int64(35), 376: np.int64(172)} | {'15:30:00': np.int64(172), '15:29:00': np.int64(35), '12:30:00': np.int64(2)} | 172 | 0 |

The frozen cache has 118,626 rows over 377 days: 186–315 rows/day (median 315), with 376 days at 315 rows. Its first cached time is 10:15:00 and its last cached time is 15:29:00; no cached row is 15:30. The 315 rows are the post-warmup feature rows from a 375-minute 09:15–15:29 input session.

Conclusion: archive vintage files do include 15:30 on many sessions, and the incumbent cache convention ends at 15:29 after feature warmup. Because the archive itself is mixed (375 and 376 bars), the port keeps 15:30 and refines validation observations rather than rewriting new files.

## 1c. Close failures

32 files fail close under the baseline last-bar rule (29 after excluding the two close+volume overlaps and the known Muhurat day 2025-10-21). Only 5 have a 15:29 close inside tolerance; the other 27 are genuine close mismatches rather than a 15:30-only artifact. Worst five by absolute selected-close error:

| date | selected close | bhavcopy close | selected error % | 15:29 close | 15:29 error |
| --- | --- | --- | --- | --- | --- |
| 2026-03-19 | 23125.7 | 23054.8 | 0.30752815032011316 | 23127.8 | 0.00316636882558079 |
| 2025-05-08 | 24207.5 | 24271.9 | -0.26532739505354525 | 24208.2 | -0.0026244340162904726 |
| 2025-04-07 | 22320.0 | 22263.8 | 0.2524277077587866 | 22320.0 | 0.002524277077587866 |
| 2026-03-24 | 22984.0 | 22928.4 | 0.2424940248774382 | 22982.9 | 0.0023769648121979726 |
| 2024-11-19 | 23491.05 | 23534.8 | -0.18589493006101604 | 23491.0 | -0.001861073814096541 |

## 1d. Volume residuals and dual expiry

| date | bars | last | expiry | units/contracts | file units | expected units | file/expected |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2025-01-14 | 376.0 | 15:30:00 | 2025-01-30 | 25.0 | 4867475.0 | 14602425.0 | 0.3333333333333333 |
| 2025-01-23 | 375.0 | 15:29:00 | 2025-01-30 | 25.0 | 4030550.0 | 12091650.0 | 0.3333333333333333 |
| 2025-01-22 | 376.0 | 15:30:00 | 2025-01-30 | 25.0 | 5294425.0 | 15883275.0 | 0.3333333333333333 |
| 2025-01-21 | 376.0 | 15:30:00 | 2025-01-30 | 25.0 | 9710850.0 | 29132550.0 | 0.3333333333333333 |
| 2025-01-20 | 376.0 | 15:30:00 | 2025-01-30 | 25.0 | 4092800.0 | 12278400.0 | 0.3333333333333333 |
| 2025-01-17 | 376.0 | 15:30:00 | 2025-01-30 | 25.0 | 5459700.0 | 16379100.0 | 0.3333333333333333 |
| 2025-01-16 | 376.0 | 15:30:00 | 2025-01-30 | 25.0 | 4279950.0 | 12839850.0 | 0.3333333333333333 |
| 2025-01-15 | 376.0 | 15:30:00 | 2025-01-30 | 25.0 | 3594750.0 | 10784250.0 | 0.3333333333333333 |
| 2025-01-30 | 376.0 | 15:30:00 | 2025-01-30 | 25.0 | 6731025.0 | 20193075.0 | 0.3333333333333333 |
| 2025-01-13 | 376.0 | 15:30:00 | 2025-01-30 | 25.0 | 6969550.0 | 20908650.0 | 0.3333333333333333 |
| 2025-01-10 | 376.0 | 15:30:00 | 2025-01-30 | 25.0 | 6542575.0 | 19627725.0 | 0.3333333333333333 |
| 2025-01-09 | 376.0 | 15:30:00 | 2025-01-30 | 25.0 | 4573775.0 | 13721325.0 | 0.3333333333333333 |
| 2025-01-08 | 376.0 | 15:30:00 | 2025-01-30 | 25.0 | 5947575.0 | 17842725.0 | 0.3333333333333333 |
| 2025-01-07 | 375.0 | 15:29:00 | 2025-01-30 | 25.0 | 4250825.0 | 12752475.0 | 0.3333333333333333 |
| 2025-01-06 | 376.0 | 15:30:00 | 2025-01-30 | 25.0 | 9534850.0 | 28604550.0 | 0.3333333333333333 |
| 2025-01-03 | 376.0 | 15:30:00 | 2025-01-30 | 25.0 | 6550100.0 | 19650300.0 | 0.3333333333333333 |
| 2025-01-02 | 376.0 | 15:30:00 | 2025-01-30 | 25.0 | 8475525.0 | 25426575.0 | 0.3333333333333333 |
| 2025-01-01 | 376.0 | 15:30:00 | 2025-01-30 | 25.0 | 4393225.0 | 13179675.0 | 0.3333333333333333 |
| 2025-01-28 | 376.0 | 15:30:00 | 2025-01-30 | 25.0 | 8099175.0 | 24297525.0 | 0.3333333333333333 |
| 2025-01-29 | 376.0 | 15:30:00 | 2025-01-30 | 25.0 | 7453075.0 | 22359225.0 | 0.3333333333333333 |
| 2025-01-24 | 376.0 | 15:30:00 | 2025-01-30 | 25.0 | 6887100.0 | 20661300.0 | 0.3333333333333333 |
| 2025-01-27 | 376.0 | 15:30:00 | 2025-01-30 | 25.0 | 6048900.0 | 18146700.0 | 0.3333333333333333 |
| 2024-12-27 | 376.0 | 15:30:00 | 2025-01-30 | 25.000010137052957 | 4932402.0 | 14797200.0 | 0.3333334684940394 |
| 2024-12-30 | 376.0 | 15:30:00 | 2025-01-30 | 25.000013044385696 | 5749603.0 | 17248800.0 | 0.33333350725847594 |
| 2024-12-31 | 376.0 | 15:30:00 | 2025-01-30 | 25.00001455618201 | 5152453.0 | 15457350.0 | 0.33333352741576017 |
| 2025-09-26 | 346.0 | 15:00:00 | 2025-09-30 | 61.43075303877665 | 5953500.0 | 7268550.0 | 0.8190767071836886 |

The 25 January-2025 residuals have file volume exactly 25×bhavcopy contracts, not 75×. They prove the 2025-01-30 expiry is still lot 25; the lot table is corrected accordingly. The remaining 2025-09-26 residual is a truncated 346-bar file ending 15:00, with raw units/contracts 61.43 and scaled volume ratio 0.819; it is bad/incomplete fetched data, not an expiry-reference ambiguity.

The same instrument ID 53001 is reused for two expiry mappings: 2025-09-25 in the early July rows and 2025-09-30 from August onward. Local UDiFF rows for the relevant transition and target dates are:

| date | expiry | close | contracts | OI |
| --- | --- | --- | --- | --- |
| 2025-07-31 | 2025-09-25 | 25014.70 | 6462 | 862425 |
| 2025-08-01 | 2025-09-30 | 24774.60 | 5577 | 913725 |
| 2025-09-29 | 2025-09-30 | 24685.00 | 95052 | 6130575 |
| 2025-09-30 | 2025-09-30 | 24612.00 | 64496 | 3831600 |

On 2025-09-29 and 2025-09-30 the only source row for 53001 is the 2025-09-30 expiry; the archive sums are exactly 95,052×75 = 7,128,900 and 64,496×75 = 4,837,200. The dual mapping is therefore not an ambiguity for these fetched days. Raw ratios around 64.1 for 2026 and 74.6 around the 2025-12 lot boundary are normal near-lot rounding/source-volume noise and remain within the existing ±5% tolerance.

## Applied changes

- Keep 15:30 rows: vintage files include them, while the frozen feature cache ends at 15:29 after warmup.
- Normalize the 2025-01-01 onward Groww OI regime by ×100; the live ingest uses the calendar OI ratio when available and only falls back to the measured boundary.
- Validate close as the volume-weighted mean of bars from 15:00 onward (NSE last-30-minute VWAP), retaining the final trade close as diagnostic info. OI remains observed at the last nonzero-OI bar.
- Mark a file ending before 15:25 as `truncated_day` plus `session_truncated`; 2025-09-26 is the only such target.
- Skip 2024-11-01 and 2025-10-21 as known Muhurat dates with `muhurat_skip`, including the missing 2024-11-01 file.
- Correct the lot-size boundary for expiry 2025-01-30 from 75 to 25; tolerances remain unchanged.

Before the 2026 extension, the post-migration local `--validate-only` report
contained 347 results: **344
passed, 1 failed** (`2025-09-26`, `session_truncated`), and **2 skipped** with
Muhurat notes. The 85 OI observations outside the strict 5% EOD comparison
are retained as `oi_timing_drift` notes when their normalized ratio remains in
the measured 0.2–2.0 envelope; the strict default used by other providers is
unchanged.

## 2026 re-fetch

The UDiFF calendar was extended through **2026-07-10** from NSE FO bhavcopy
archives. Missing files for 2026-05-26 through 2026-07-10 were requested with a
browser-like user agent and polite retries; weekends and 404 holiday dates were
skipped. The regenerated calendar has 621 rows and no zero-OI front rows.

The explicit 2026-04-01 through 2026-06-30 refetch plan contained **60 trading
days** across the 2026-04-28, 2026-05-26, and 2026-06-30 front contracts. Groww
fetched and overwrote **60/60** files with no request failures. This includes
the **43 known Kite wrong-contract files** and the missing June sessions. The
isolated 2026-06-03 file was retried once; Groww returned the same 375-bar file
with zero volume, so its close/volume validation failure is retained as a
vendor-quality anomaly.

The required 2025-09-26 retry also fetched successfully at the request level,
but again returned **346 bars ending 15:00:00**. It remains a permanent vendor
gap, recorded as `session_truncated`.

The optional July extension succeeded for 2026-07-01 through 2026-07-10 (eight
trading days, 375 bars/day) using Groww's deprecated short-lookback endpoint
and live symbol `NIFTY26JULFUT`, written as archive symbol `NIFTY-I`. The
endpoint returned cumulative volume and no OI; volume was differenced into
per-minute units before writing, while `oi=0` is explicitly unsuitable for OI
validation. A probe using `NIFTY-I` itself returned no candles.

Full validation from 2024-11-01 through 2026-07-10: **403 passed, 10 failed,
2 skipped**. The failures are: one `session_truncated` (2025-09-26), one
2026-06-03 `close` plus `volume` failure, and eight July `oi` failures caused
by the optional endpoint's missing OI. The skipped rows are the known Muhurat
dates 2024-11-01 and 2025-10-21.

### Re-fetched consistency samples

The values below use the validator's 15:00 onward volume-weighted close,
whole-day volume, and last nonzero OI. Expected volume is bhavcopy contracts
times the 2026 lot size of 65.

| trade date | front expiry | last-30m VWAP | bhavcopy close | day volume | bhavcopy × 65 | last nonzero OI | bhavcopy OI |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2026-04-15 | 2026-04-28 | 24236.8881 | 24237.7 | 4,196,855 | 4,232,540 | 18,745,600 | 17,909,580 |
| 2026-05-15 | 2026-05-26 | 23644.2849 | 23643.9 | 5,142,800 | 5,147,675 | 17,699,600 | 17,040,140 |
| 2026-06-15 | 2026-06-30 | 23918.1138 | 23916.6 | 4,654,455 | 4,684,940 | 18,025,400 | 17,817,930 |
