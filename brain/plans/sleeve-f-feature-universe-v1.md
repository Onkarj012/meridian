# Sleeve F — Feature Universe & Information-Family Roadmap v1

**Date:** 2026-07-11
**Status:** PLANNING ONLY. Companion to `sleeve-f-model-data-roadmap-v1.md` (C1 k=95, C2 k=101 unchanged). Governs what information families exist, how they get admitted, and in what order — for Campaign 3 and beyond.
**Authors:** Claude Fable 5 × gpt-5.6-sol, two adversarial passes. Transcripts: scratchpad `fable_features_v0.md`, `sol_features_r1.md`, `fable_features_rebuttal.md`, `sol_features_r2.md`.
**Prompted by (principal's thesis):** intraday trading is news-sensitive; global cascades (US → Japan/Korea/HK → India) and events (wars) move markets; therefore train on a broad spectrum — stocks, sector/industry, index, global economic, global markets, news, sentiment — include broadly, then keep/remove what works.

---

## 1. Verdict on the principal's thesis (joint, final)

- **The economics are right.** Global cascades, news, sector confirmation, and overseas India price discovery can affect NIFTY after the opening auction. The opening print is NOT a sufficient statistic: cross-market disagreement, post-open innovations (a 12:47 ES shock), and NIFTY's *response ratio* to global moves remain live intraday channels. Session-overlap fact base: ES/NQ, Brent, USDINR, GIFT Nifty overlap our entire 09:45–14:29 decision window; Korea cash runs to 12:00 IST, Japan to 12:00 IST (post-Nov-2024), HK to 13:30 IST.
- **The tactic is wrong.** With ~770 walk-forward trades, a spent ~170-trade holdout, and 150 forward trades per promotion, "include everything, prune what works" mainly selects noise. Sampling error at 770 trades is ±1.8 pp on a 50% win rate; a real 2 pp conditional lift is barely distinguishable from luck *before* serial dependence and multiplicity.
- **Resolution:** the broad-spectrum thesis belongs in **data acquisition** (append-only, point-in-time warehouse, start now); the selectable model gets **one coherent frozen information family per campaign**, judged only by forward shadow.
- **Sparse-event arithmetic kills event dummies:** RBI MPC ≈ 27–29 lifetime Sleeve F trades, budgets ≈ 6, elections ≈ 2, wars ≈ 1–3. Event *clocks* can condition; event *identities* cannot be learned.
- **News survives only as timestamped event flow**, not prose sentiment: native NSE announcements (exchange dissemination timestamps), scheduled release clocks, macro actual-minus-vintage-consensus surprise, and observed cross-market *reaction* to information. The lake's `sentiment/` tree is quarantine material (≈15k rows, timestamps mechanically 07:00/08:00, daily aggregates, empty GDELT file, mixed schemas) — reject the archive, not the concept.

## 2. What "keep what works" legally means here

- **ILLEGAL:** train broad, drop what looks dead on OOS folds, report survivors as pre-specified (deleted-variable bias; spends OOS silently).
- **LEGAL-1 — screening inside the frozen learner**, valid only under ALL of: universe+transforms frozen beforehand; screen and every constant frozen beforehand; training rows only; recomputed per fold; no outer result feeds back; no silent screen variants; missingness cannot encode vendor eras; final artifact reruns the identical procedure. Even then it's honest, not low-variance — nuisance features still add chance-split variance.
- **LEGAL-2 (workhorse) — family-level registered contrasts:** same learner ± one frozen family, k+1 per contrast (the C2-W vs C2-P pattern).
- **LEGAL-3 — diagnostics on already-spent data**, reported, never selecting.

## 3. Tier table (final, supersedes draft ordering)

| Tier | Family | Status |
|---|---|---|
| 0 | C1 protocol v1; C2 futures+calendar+VIX (C2-W/C2-P) | Locked, k=95 / k=101 |
| Audit lane (start now) | ES/NQ + Asia minute feeds; pre-open snapshot vector; GIFT–SGX seam; NSE announcements archive; options timestamps/continuation; ADR/ETF closes; official sector indices; FII/DII provenance; USDINR/Brent | Data contracts only; no label/PnL inspection; burns no k |
| 1 (best C3 if contract passes) | **Intraday ex-India shock/response** — 14 frozen features (§5) | Needs minute-feed audit pass |
| 1-alt | **Pre-open cross-market snapshot/response** — reduced vector, 4 clocks (08:00/09:00/09:15/09:45 IST) | ~80% of the pre-open hypotheses' value at a fraction of cost; separately quoted contract; loses post-09:45 innovations |
| 2 | **Options state** — highest expected 60-min relevance if intraday timestamps + live continuation prove out | Competes at top of C3 fallback order |
| 2 | **ADR/ETF overnight India discovery** — INFY, HDB, IBN, WIT, INDA, EPI official closes; India-specific, PIT-provable, ~3–5 days effort | Promoted in debate from #5 → default fallback after options |
| 2 | **Native announcement intensity** — counts/weights/category/minutes-since-release; no polarity models initially | Sparse exposure, hardest archive proof |
| 3 | Daily India internal-state: official sector-index dispersion (7 indices), proven FII/DII (turnover-scaled, disagreement, persistence) | Defensible context; mostly absorbed by gap + first 30 min; never appended opportunistically to other families |
| 3 | Pre-open daily global context (T-1 US/Asia closes, US 10Y) | Mostly decoration once gap/VIX/first-30-min exist |
| Deferred (unchanged) | Breadth (needs historical constituents or genuine pre-2020 fixed panel), BankNifty price-only confirmation, spot/basis, OI | Per roadmap §7 |
| X | Current sentiment lake; LLM-scored retrospective headlines (archive vintage + scorer contamination + prompt DoF); war/election identity dummies; post-hoc pruning; "test intraday only if daily works" inference | Rejected |

## 4. C3 Six-Week Gate Cascade (committed)

Clock starts at C2 artifact freeze. At **C2-freeze + 6 weeks**, the first family whose data contract passes is irrevocably designated C3:

`minute ex-India → snapshot cross-market → options → ADR/ETF discovery → announcements → wait (no manufactured C3)`

- Weeks 0–2 commercial terms + raw samples + live capture starts; weeks 2–4 timestamp/session/seam/coverage/license validation (no labels); weeks 4–5 historical/live parity + failure injection; week 6 one signed, hashed gate report → branch selected.
- Selection uses provenance/coverage/latency/licensing/live-parity ONLY — never PnL, IC, labels, or attribution. No deadline extensions to rescue a preferred feed; a late-passing feed waits for C4.
- C3 = one frozen family contrast, **k=102**; historical WF is development evidence; promotion needs ≥150 fresh forward trades. C3 historical development and shadow begin only after the C2 prospective decision.

## 5. Frozen sketch — intraday ex-India shock/response family (14 features)

`global_equity_shock_15m/60m` (median standardized return across ES, NQ, Nikkei, KOSPI, HSI); `asia_cash_shock_since_0915`; `us_futures_innovation_since_0915`; `global_risk_concordance_15m` (sign-agreement fraction); `cross_market_dispersion_15m` (MAD); `gift_nifty_lead_5m/15m`; `gift_nifty_dislocation` (log ratio vs trailing causal same-minute median); `usdinr_shock_30m`; `oil_in_inr_shock_60m` (Brent×USDINR); `nifty_global_relative_strength_15m`; `nifty_open_response_ratio` (clipped); `shock_persistence` (15m vs 60m sign agreement).

Standardization: preceding 60 completed sessions, matched by minute of day. Equal frozen composite weights; **no fitted beta anywhere** (a beta-weighted "global-implied gap" was rejected as a model hidden inside a feature; if ever revived: ridge on prior 252 sessions, per fold, rolled prospectively). Row eligibility requires ES + GIFT + USDINR + Brent + ≥2 of Nikkei/KOSPI/HSI live; missingness indicators prohibited. The snapshot branch gets its own smaller registration — it may not carry intraday fields as constants/NaNs.

## 6. Audit-lane contracts (go/no-go summaries; full criteria in debate transcript sol_features_r2 §2a)

| Feed | Go requires (essence) | Effort |
|---|---|---:|
| ES/NQ + Asia minute | Native timestamps, DST/session calendars, roll convention, ≥98% eligible bars from 2021, license usable, ≥60-session live parity | 8–12 pd + procurement |
| Snapshot vector | 4 clocks reconstructible ±60s, quote age ≤2 min, ≥99% sessions from 2021, live equivalent exists | 4–6 pd |
| GIFT–SGX | Documented SGX→GIFT 2023 seam, continuous economic series from 2021, causal trading-date alignment | 6–10 pd |
| NSE announcements | Exchange-native dissemination timestamps, append-only corrections, sampled completeness proof, causal membership/weight join | 10–15 pd + ≥60 live sessions |
| Options | True intraday pre-decision observations, ≥95% decision minutes with valid near-expiry surface, CSV↔parquet seam reconciles, post-2026 continuation | 12–18 pd |
| ADR/ETF closes | 6 instruments from 2021 (pre-frozen min 4), corporate-action ledger causal, unambiguous US→next-India-session join | 3–5 pd |
| Sector indices | 7-index official panel from 2021, methodology vintage known | 2–4 pd |
| FII/DII | Historical publication times independently evidenced (18:30 hardcode is code, not provenance), versioned corrections | 4–7 pd |
| USDINR/Brent | Exact instrument pinned (not RBI reference rate), causal rolls, ≥98% bars or ≥99% snapshots | 5–8 pd |

Thresholds are data-contract gates, not performance filters — failures cannot be repaired by checking which gaps hurt PnL.

## 7. Append-only live collectors — start now (historyless feeds accumulate or die)

GIFT Nifty 1-min + contract identity; synchronized ES/NQ/Nikkei/KOSPI/HSI/USDINR/Brent 1-min with exchange+receive timestamps and payload hashes; independent snapshot ledger at the 4 IST clocks (not resampled later); NSE announcements raw payload + native dissemination time + first-seen + amendment events; timestamped raw option-chain snapshots; ADR/ETF + sector closes with publication times and action files; FII/DII raw release artifacts with corrections as new versions. Never overwrite; vendor corrections are appended versions.

## 8. Known repo hooks

`ingest/announcements.py` already enforces `available_at <= decision_time`; `ingest/gdelt.py`/`ingest/news.py` quarantine by construction (keep); `ingest/fii_dii.py` 18:30 release helper (provenance still unproven); `ingest/macro.py` release-timestamp pattern (default-to-now unsafe for vintages — fix when used); `ingest/yfinance.py` reconciliation-only (must not become a primary history source).
