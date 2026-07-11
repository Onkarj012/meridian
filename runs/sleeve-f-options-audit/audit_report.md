# Options archive audit report

**Status:** scan/report only; no verdict. All evidence is sampled unless explicitly labeled inventory.

## Scope and sampling

- Roots: `/Users/onkarj012/Projects/market/intranet_optinet/data/option_data`, `/Users/onkarj012/Projects/market/intranet_optinet/data/parquet`
- Candidate option dates: 2020-01-01 to 2026-07-10; sampled 30 days.
- Basis: stratified early/middle/recent plus four Thursday expiry-week anchors. The sample includes early, middle, recent, and four Thursday expiry-week anchors where available.
- Inventory: 5,631 option CSVs and 2,164 option Parquet files; all supplied files: 10,615.

## Timestamps and cadence (sample basis)

CSV rows carry `date` + `time` at intraday grain; no separate fetch timestamp was observed, and timestamps are timezone-naive/unspecified. Parquet option rows carry `date` only, so they are EOD/date-grain for this audit. Cadence below is computed after sorting unique CSV timestamps per sampled day.

| Day | CSV rows | timestamps | median gap (min) | p95 gap | max gap | unique gap examples | valid-surface minutes / observed minutes |
|---|---:|---|---:|---:|---:|---|---:|
| 2020-01-01 | 25,105 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 375 / 376 (99.7%) |
| 2020-01-02 | 21,242 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 375 / 376 (99.7%) |
| 2020-01-03 | 29,735 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 375 / 376 (99.7%) |
| 2020-01-06 | 35,030 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 375 / 376 (99.7%) |
| 2020-01-07 | 35,356 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 376 / 376 (100.0%) |
| 2020-01-08 | 36,833 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 376 / 376 (100.0%) |
| 2020-01-09 | 30,610 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 375 / 376 (99.7%) |
| 2020-01-10 | 36,521 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 375 / 376 (99.7%) |
| 2020-01-13 | 33,563 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 375 / 376 (99.7%) |
| 2020-01-14 | 31,840 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 376 / 376 (100.0%) |
| 2020-01-15 | 30,467 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 376 / 376 (100.0%) |
| 2020-01-16 | 25,042 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 375 / 376 (99.7%) |
| 2020-01-17 | 30,817 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 375 / 376 (99.7%) |
| 2020-01-20 | 32,499 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 375 / 376 (99.7%) |
| 2020-01-21 | 32,596 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 375 / 376 (99.7%) |
| 2020-01-22 | 31,686 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 375 / 376 (99.7%) |
| 2020-01-23 | 27,519 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 375 / 376 (99.7%) |
| 2020-01-24 | 39,187 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 375 / 376 (99.7%) |
| 2020-01-27 | 39,382 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 375 / 376 (99.7%) |
| 2020-10-21 | 53,194 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 376 / 376 (100.0%) |
| 2021-08-12 | 60,252 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 375 / 376 (99.7%) |
| 2021-08-13 | 60,871 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 376 / 376 (100.0%) |
| 2022-04-07 | 81,892 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 375 / 376 (99.7%) |
| 2023-03-23 | 72,957 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 376 / 376 (100.0%) |
| 2023-03-29 | 85,248 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 376 / 376 (100.0%) |
| 2024-03-21 | 86,654 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | 375 / 376 (99.7%) |
| 2024-11-07 | 751 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | n/a |
| 2024-11-14 | 752 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | n/a |
| 2025-09-10 | 751 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | n/a |
| 2026-07-10 | 375 | intraday | 1.0 | 1.0 | 1.0 | [1.0] | n/a |

## Strike/expiry mapping

CSV contracts encode underlying, expiry, strike, and CE/PE in the symbol; Parquet has explicit columns. Unparseable CSV identifier count in the sampled CSV rows is **2** (examples: BANKNIFTY-I, NIFTY-I). This is not a full-archive identifier verdict.

## Valid-surface proxy

Per timestamp, the proxy sets ATM to the median active strike, requires nonzero `close`/LTP, and requires at least N=5 distinct strikes below and above ATM for both CE and PE. This is an audit assumption, not a market-data validity standard; the denominator is observed CSV timestamps/minutes, not an exchange session calendar.

## CSV ↔ Parquet seam (sample basis)

| Date | CSV final-contract rows | Parquet rows | joined contracts | exact close matches | nonzero drift | max abs drift |
|---|---:|---:|---:|---:|---:|---:|
| 2022-04-07 | 400 | 4,150 | 398 | 114 | 284 | 294.0 |
| 2023-03-23 | 281 | 2,611 | 279 | 67 | 212 | 155.5 |
| 2023-03-29 | 374 | 2,577 | 372 | 37 | 335 | 153.79999999999995 |
| 2024-03-21 | 338 | 2,964 | 336 | 101 | 235 | 108.75 |
| 2024-11-07 | 2 | 2,620 | 0 | 0 | 0 | n/a |
| 2024-11-14 | 2 | 2,555 | 0 | 0 | 0 | n/a |
| 2025-09-10 | 2 | 2,374 | 0 | 0 | 0 | n/a |

The seam is a grain change, not a clean same-row format conversion: CSV contains intraday observations while Parquet contains one date-level observation per contract. Overlap-day join results above are sampled and do not establish full seam reconciliation.

## Live continuation

Most recent filename-derived option date: **2026-07-10**; gap to assumed today (2026-07-12): **2 days**. Inventory includes 2026 Parquet partitions, but this read-only scan cannot establish whether anything currently appends; filesystem modification times and a writer/process audit would be needed.

## Reviewer conclusion inputs

This report intentionally issues no go/no-go verdict. The load-bearing evidence is that the legacy CSV archive is intraday but timezone-unspecified and the Parquet continuation is date-only/EOD, while cadence, valid-surface coverage, and seam drift are sample-dependent. A reviewer should treat the four cascade criteria as requiring deeper audit unless the sampled evidence is judged sufficient.
