# Sleeve F Campaign C3 — H1 admission decision

**Status: `REJECTED_AT_D4_PRE_REGISTRATION`**

- Decision date: 2026-07-20
- Ratified by: principal (Onkar), on Sol's post-D4 ruling of 2026-07-20
- Registration frozen: **false** (rejection occurred at the D4 admission audit, before registration freeze)
- Ratification text: "Ratify `REJECTED_AT_D4_PRE_REGISTRATION` for C3-H1/yfinance, preserve the frozen D4 definition without amendment, park this cascade position, and advance to the pre-open snapshot family."

## Rejected object

C3-H1: daily/overnight ex-India shock family (24 features + 9 flags), source contract = Yahoo Finance daily bars via yfinance.

| Source | yfinance ticker | Venue calendar |
|---|---|---|
| us_es | ES=F | CMES (exchange_calendars 4.11.1) |
| us_nq | NQ=F | CMES |
| nikkei | ^N225 | XTKS |
| hsi | ^HSI | XHKG |
| kospi | ^KS11 | XKRX |
| usdinr | INR=X | none (OTC; all-weekday denominator, no absence excusal) |
| brent | BZ=F | XLON (documented ICE Futures Europe proxy) |

## D4 audit identity

- Audit interval: 2020-01-01 → 2026-07-10 (1,610 NSE sessions)
- Report: `runs/sleeve-f-c3-d4-audit/report.md` / `report.json`
- Deterministic content SHA-256: `be8e5415179b99f595d5fc53e241c67b62f7f835b7369c8cb5b36844b0508d7e`
- Calendar manifest SHA-256: `83dad00a95f9b234fc0361903657a938ac97c50cb0dbcd0f5f9eae34e0e70359`
- Snapshot set: `snapshot-20260719T134356Z-nse` (per-source parquet and manifest SHA-256 values enumerated in the report)

## Gate results

| Gate | Result |
|---|---|
| Per-source coverage — nikkei 99.69% / hsi 99.69% / kospi 99.56% / usdinr 99.65% | PASS |
| Per-source coverage — us_es 97.21%, us_nq 97.21%, brent 97.51% (threshold 98%) | **FAIL** |
| Complete-row coverage — 1,255/1,382 = 90.81% (threshold 98%) | **FAIL** |
| Consecutive-missing (max run 4) | PASS |
| Timestamp/session mapping (3,500 sampled, 100%) | PASS |
| Duplicates / stale | PASS |
| **Overall** | **REJECT** |

Residual ES/NQ/Brent misses are predominantly CME/ICE partial and early-close sessions for which Yahoo publishes no daily bar. Under the frozen D4 definition (Sol Q1 ruling, 2026-07-19) partial and early-close sessions are expected-open and vendor gaps on them are not excusable; this is a source deficiency of the registered vendor contract.

## Blindness attestations

- `labels_inspected: false`
- `returns_inspected: false`
- `model_outputs_inspected: false`
- `holdout_accessed: false`

The D4 audit was outcome-blind: coverage, timestamps, calendar structure, duplicates, and finite positive closes only. No C3 walk-forward, label computation, or model training run was executed. No candidate was selected and no C3 walk-forward is authorized.

## Prohibitions (Sol post-D4 ruling, 2026-07-20)

1. **No partial-session excusal amendment.** Amending the D4 denominator after gate results are known is coverage-conditioned gate modification and is impermissible, regardless of the amendment's mechanical reproducibility.
2. **No imputation and no source substitution inside H1.** The registered rejection text stands as written.
3. Any ES/NQ/Brent source upgrade is permissible only as a separately frozen H1′ registration with its own identity, a fully pre-frozen vendor contract, one full-history admission attempt, and a separately ratified roadmap version — it is not a continuation of C3-H1. An H1′ admission failure irrevocably ends vendor attempts for this cascade position.
4. The Yahoo snapshots, their manifests, and the D4 report are immutable failed provenance. They must never be reused or reinterpreted as admission evidence for any future registration. Outcome-blind machinery (contracts, calendars, causal mapping, audit gates, matrix/replay code, tests) is infrastructure and may be reused after version review and hashing.

## Cascade disposition

The `minute ex-India` cascade position (executed at H1 daily/overnight granularity per the 2026-07-19 principal directive) failed admission and is parked. It cannot be reopened inside this cascade. Per the committed order, the cascade advances to: **snapshot cross-market** → options → licensed media event-flow → ADR/ETF discovery → native announcements → wait.
