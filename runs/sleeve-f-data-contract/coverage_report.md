# Coverage report

Source calendar span: 2020-01-01 to 2026-07-10; requested span was 2020-01-01 to 2026-07-10.

## Bhavcopy vs minute-data days

| Year | Bhavcopy trading days | Minute-data days on disk | Difference |
|---:|---:|---:|---:|
| 2020 | 251 | 252 | -1 |
| 2021 | 247 | 248 | -1 |
| 2022 | 248 | 248 | 0 |
| 2023 | 245 | 246 | -1 |
| 2024 | 249 | 248 | 1 |
| 2025 | 248 | 248 | 0 |
| 2026 | 127 | 127 | 0 |

Minute files are identified by `nifty_fut_DD_MM_YYYY.csv` under the supplied minute root; `1617` unique dates were found in total. Duplicate minute-date paths: `0`.

## Gap days

`gap_days.csv` contains `1` bhavcopy trading days in 2024-11-01 through 2026-03-30 with no minute CSV.

## Source and filter audit

UDiFF NIFTY `FinInstrmTp` values observed: `IDF, IDO`. Counts across all NIFTY rows: `{'IDF': 1497, 'IDO': 830119}`.
The calendar filter is UDiFF `TckrSymb == NIFTY` and `FinInstrmTp == IDF`; `IDF` is the index-futures type. `IDO` is excluded as the index-options type. Futures rows also have empty strike and option-type fields in the observed UDiFF data. Legacy rows use `SYMBOL == NIFTY`, `INSTRUMENT == FUTIDX`, zero strike, and `OPTION_TYP == XX`.

| Source | Year | Bhavcopy ZIP files | Files with parsed trade dates |
| --- | ---: | ---: | ---: |
| legacy | 2020 | 251 | 251 |
| legacy | 2021 | 247 | 247 |
| legacy | 2022 | 248 | 248 |
| legacy | 2023 | 245 | 245 |
| legacy | 2024 | 125 | 125 |
| udiff | 2024 | 124 | 124 |
| udiff | 2025 | 248 | 248 |
| udiff | 2026 | 127 | 127 |

UDiFF files loaded: `499`; legacy files loaded: `1116`. UDiFF dates take precedence. Legacy dates used after precedence filtering: `1116`.
Legacy files do not contain `FinInstrmId`; their `fin_instrm_id` values in the CSVs are deterministic synthetic IDs of the form `legacy:NIFTY:FUTIDX:YYYY-MM-DD`, and are not native NSE UDiFF IDs.

## Archive-date anomalies

File-level parse/filter anomalies (corrupt ZIP, missing required columns, unparseable trade date, or empty filtered futures rows): None; every source ZIP parsed with the required columns and contained the expected filtered futures rows.

Weekday-calendar comparison (Mon-Fri dates absent from the observed bhavcopy date set):
- 2020: 12 weekday dates absent (likely exchange holidays/source gaps): 2020-02-21, 2020-03-10, 2020-04-02, 2020-04-06, 2020-04-10, 2020-04-14, 2020-05-01, 2020-05-25, 2020-10-02, 2020-11-16, 2020-11-30, 2020-12-25
- 2021: 14 weekday dates absent (likely exchange holidays/source gaps): 2021-01-26, 2021-03-11, 2021-03-29, 2021-03-30, 2021-04-02, 2021-04-14, 2021-04-21, 2021-05-13, 2021-07-21, 2021-08-19, 2021-09-10, 2021-10-15, 2021-11-05, 2021-11-19
- 2022: 12 weekday dates absent (likely exchange holidays/source gaps): 2022-01-26, 2022-03-01, 2022-03-18, 2022-04-14, 2022-04-15, 2022-05-03, 2022-08-09, 2022-08-15, 2022-08-31, 2022-10-05, 2022-10-26, 2022-11-08
- 2023: 15 weekday dates absent (likely exchange holidays/source gaps): 2023-01-26, 2023-03-07, 2023-03-30, 2023-04-04, 2023-04-07, 2023-04-14, 2023-05-01, 2023-06-29, 2023-08-15, 2023-09-19, 2023-10-02, 2023-10-24, 2023-11-14, 2023-11-27, 2023-12-25
- 2024: 16 weekday dates absent (likely exchange holidays/source gaps): 2024-01-22, 2024-01-26, 2024-03-08, 2024-03-25, 2024-03-29, 2024-04-11, 2024-04-17, 2024-05-01, 2024-05-20, 2024-06-17, 2024-07-17, 2024-08-15, 2024-10-02, 2024-11-15, 2024-11-20, 2024-12-25
- 2025: 13 weekday dates absent (likely exchange holidays/source gaps): 2025-02-26, 2025-03-14, 2025-03-31, 2025-04-10, 2025-04-14, 2025-04-18, 2025-05-01, 2025-08-15, 2025-08-27, 2025-10-02, 2025-10-22, 2025-11-05, 2025-12-25
- 2026: 10 weekday dates absent (likely exchange holidays/source gaps): 2026-01-15, 2026-01-26, 2026-03-03, 2026-03-26, 2026-03-31, 2026-04-03, 2026-04-14, 2026-05-01, 2026-05-28, 2026-06-26

Source file/date duplicate groups: `0`; duplicate filtered futures `(trade_date, instrument_id)` groups: `0`.
Instrument IDs observed with more than one expiry date: `53001`. This is retained as observed source data and is relevant around the 2025 expiry-weekday migration.
Front-month rows with zero OI: `0`. Dates: none.
78 calendar rows have front_expiry equal to trade_date; the expiring contract is therefore retained on expiry day by construction.
No expiry-day front rows have zero OI.

## Expiry weekday observation

Observed weekday transitions in `expiries.csv`: 2023-01-25 (Thursday -> Wednesday), 2023-02-23 (Wednesday -> Thursday), 2023-03-29 (Thursday -> Wednesday), 2023-04-27 (Wednesday -> Thursday), 2023-06-28 (Thursday -> Wednesday), 2023-06-29 (Wednesday -> Thursday), 2025-09-30 (Thursday -> Tuesday), 2026-03-30 (Tuesday -> Monday), 2026-04-28 (Monday -> Tuesday).
The weekday is derived from each observed expiry date; no weekday convention is hardcoded into contract selection.

## Roll-evidence availability

121 evidence rows had a minute file; 1 were missing.
The supplied UDiFF archive extends through 2026-07-10; minute files are compared only against bhavcopy rows present in these inputs.

## Expiry-day selection rule

For each bhavcopy trading day, eligible futures are those with expiry greater than or equal to the trade date. Sorting by expiry makes the contract expiring on the trade date the front month on expiry day; the next trading session naturally selects the following contract.
