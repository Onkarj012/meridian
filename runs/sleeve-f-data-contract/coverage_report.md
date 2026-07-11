# Coverage report

Source calendar span: 2024-01-01 to 2026-05-25; requested span was 2024-01-01 to 2026-06-30.

## Bhavcopy vs minute-data days

| Year | Bhavcopy trading days | Minute-data days on disk | Difference |
|---:|---:|---:|---:|
| 2024 | 246 | 209 | 37 |
| 2025 | 248 | 0 | 248 |
| 2026 | 95 | 43 | 52 |

Minute files are identified by `nifty_fut_DD_MM_YYYY.csv` under the supplied minute root; `1246` unique dates were found in total. Duplicate minute-date paths: `0`.

## Gap days

`gap_days.csv` contains `347` bhavcopy trading days in 2024-11-01 through 2026-03-31 with no minute CSV.

## Source and filter audit

UDiFF NIFTY `FinInstrmTp` values observed: `IDF, IDO`. Counts across all NIFTY rows: `{'IDF': 1392, 'IDO': 769510}`.
The calendar filter is UDiFF `TckrSymb == NIFTY` and `FinInstrmTp == IDF`; `IDF` is the index-futures type. `IDO` is excluded as the index-options type. Futures rows also have empty strike and option-type fields in the observed UDiFF data. Legacy rows use `SYMBOL == NIFTY`, `INSTRUMENT == FUTIDX`, zero strike, and `OPTION_TYP == XX`.

UDiFF files loaded: `464`; legacy files loaded: `125`. UDiFF dates take precedence. Legacy 2024 was used for `125` dates not covered by UDiFF.
Legacy files do not contain `FinInstrmId`; their `fin_instrm_id` values in the CSVs are deterministic synthetic IDs of the form `legacy:NIFTY:FUTIDX:YYYY-MM-DD`, and are not native NSE UDiFF IDs.

## Archive-date anomalies

Weekday-calendar comparison (Mon-Fri dates absent from the observed bhavcopy date set):
- 2024: 16 weekday dates absent (likely exchange holidays/source gaps): 2024-01-22, 2024-01-26, 2024-03-08, 2024-03-25, 2024-03-29, 2024-04-11, 2024-04-17, 2024-05-01, 2024-05-20, 2024-06-17, 2024-07-17, 2024-08-15, 2024-10-02, 2024-11-15, 2024-11-20, 2024-12-25
- 2025: 13 weekday dates absent (likely exchange holidays/source gaps): 2025-02-26, 2025-03-14, 2025-03-31, 2025-04-10, 2025-04-14, 2025-04-18, 2025-05-01, 2025-08-15, 2025-08-27, 2025-10-02, 2025-10-22, 2025-11-05, 2025-12-25
- 2026: 8 weekday dates absent (likely exchange holidays/source gaps): 2026-01-15, 2026-01-26, 2026-03-03, 2026-03-26, 2026-03-31, 2026-04-03, 2026-04-14, 2026-05-01

Source file/date duplicate groups: `0`; duplicate filtered futures `(trade_date, instrument_id)` groups: `0`.
Instrument IDs observed with more than one expiry date: `53001`. This is retained as observed source data and is relevant around the 2025 expiry-weekday migration.
Front-month rows with zero OI: `0`. Dates: none.
28 calendar rows have front_expiry equal to trade_date; the expiring contract is therefore retained on expiry day by construction.
No expiry-day front rows have zero OI.

## Expiry weekday observation

Observed weekday transitions in `expiries.csv`: 2025-09-30 (Thursday -> Tuesday), 2026-03-30 (Tuesday -> Monday), 2026-04-28 (Monday -> Tuesday).
The weekday is derived from each observed expiry date; no weekday convention is hardcoded into contract selection.

## Roll-evidence availability

21 evidence rows had a minute file; 1 were missing.
The supplied 2026 UDiFF archive ends on 2026-05-25, so later June 2026 expiry sessions cannot be compared against a bhavcopy row from these inputs. Minute files after that date are not used to invent bhavcopy values.

## Expiry-day selection rule

For each bhavcopy trading day, eligible futures are those with expiry greater than or equal to the trade date. Sorting by expiry makes the contract expiring on the trade date the front month on expiry day; the next trading session naturally selects the following contract.
