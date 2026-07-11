# Sleeve F — Model Training & Selection Protocol v1

**Date:** 2026-07-11
**Status:** APPROVED (user, 2026-07-11) — decisions §10 locked. Nothing executed yet.
**Authors:** Claude Fable 5 × gpt-5.6-sol (two planning rounds), facts by gpt-5.6-luna inventory (`brain/docs/data-inventory-2026-07.md` + pipeline fact sheet, 2026-07-11)
**Supersedes:** the "2025 data missing / 43 days of 2026" premises in `sleeve-f-joint-forward-plan.md` and `sleeve-f-sol-review-full.md` — data contract is complete (futures + spot 1-min, 2020-01-01→2026-07-10, validated). All other discipline from those docs carries forward.

---

## 0. Framing decisions (agreed Fable × Sol)

1. **There is no deployable incumbent.** `models/artifacts/sleeve_f/final_long.lgb` was trained against proxy features (volume≡1, OI≡0, spot≡futures close — ~10 of 39 features degenerate) and its 2.17/2.07 Sharpe numbers come from the frozen proxy cache marked `promotable: false`. The frozen model is an **attribution benchmark only**. Every deployable candidate is trained fresh on real data under this protocol.
2. **Selection and attribution are separate tracks.** The attribution ladder (§3.4) explains where the legacy 2.17 came from; it can never be promoted and its runs sit outside the selection experiment count (they cannot alter protocol specs once frozen).
3. **Labels must be execution-consistent** for anything deployable. The legacy close-touch label generator is rebuilt only to prove parity with the archive; prospective candidates train on outcomes the replay can actually realize.
4. **Pre-registration is mechanical, not aspirational.** Everything in §4 is committed to git *before* the first training run; the runner verifies git-tracked status (the old `committed_grid: true`-without-checking failure mode is banned).

---

## 1. Phase 0 — Derived data & label build

Inputs are already validated; this phase builds what training consumes.

**1a. Two label generators (both committed with tests):**
- **Legacy-parity generator:** close-touch, entry = current futures close, long = close reaches entry×1.0040 before entry×0.9970 within 60 min (mirror of `archive/scripts/sprint1_futures.py:119-165`). Sole purpose: byte-level parity against the archived barrier-label parquet in `intranet_optinet/models/router_v0/`. If parity fails → stop, diagnose.
- **Execution-consistent generator (used for all deployable candidates):** decision at bar close → entry at next bar open → barriers **measured from actual entry price** (+0.40% / −0.30%) → OHLC first-touch, stop wins double-touch → 60-min same-session timeout at close. Target for classifier candidates: binary label = 1 if net PnL on the realized exit (the barrier touch or the 60-min same-session timeout close, per this bullet's replay rule) is strictly positive after era-dated per-trade costs (§1d/§5); label = 0 otherwise, including the net_pnl == 0 case (ties treated as non-profitable). For candidate C: net return in R units (§5).

**1b. Real-feature matrix:** all 39 features from `features/sleeve_f_router.py` computed on real futures volume/OI and real spot close, 2020-01-01→2026-06-30, per-day frames, rows end 15:29, warm-up rows (first 60 min lookback window per day, i.e. pre-09:45 eligibility unchanged) excluded from training.

**1c. Exclusion & missing-data policy (pre-registered, data-driven):**
| Date/condition | Policy |
|---|---|
| 2025-09-26 (truncated 15:00) | drop day entirely (labels near close invalid) |
| 2026-06-03 (zero volume, bad close) | drop day entirely |
| 2026-06-01, 2026-06-04 (spot gaps) | keep day; spot/basis features → NaN; never impute across gap |
| 2026-07-01→10 (no OI) | outside protocol span; shadow only (§8) |
| Muhurat 2024-11-01, 2025-10-21 | naturally absent |
| Zero-denominator / warm-up rows | NaN per existing feature-builder behavior; NaN handling frozen in the registration doc, not left to LightGBM defaults silently |

**1d. Era metadata table:** date-versioned lot size (50 / 25 from 2025-01 / 75 from 2025-09-30 / 65 from 2026-01), STT (0.0125% / 0.02% from 2024-10-01 / 0.05% from 2026-04-01), NSE txn, brokerage, slippage — consumed by cost engine (§5). Replaces the bridge's flat ₹105.

---

## 2. Phase 1 — Pre-registration freeze (git commit BEFORE any run)

Frozen document + hashed configs covering:
- data snapshot hashes + exclusion table (§1c);
- both label generator specs + parity results;
- exact 39-feature list + NaN policy;
- replay state machine (§4c) — every ambiguity pinned;
- threshold-fitting rule (§4b);
- risk normalization + sizing (§5);
- cost schedule + stress levels;
- fold grid (§4a), purge/embargo definition in timestamps;
- candidate specs (§3) — hyperparameters fixed, no tuning;
- baseline matching mechanics + seed derivation;
- metrics, viability gates, replacement rule, kill criteria (§6);
- holdout window + one-shot open rule (§7);
- cumulative experiment count k (§6d);
- minimum trade counts (hard gates).

No new signal families, features, geometries, or gates may be added after the freeze. Any change = protocol v2, new registration, k increments.

---

## 3. Candidates

**A. Retrained router (reference candidate).** LightGBM binary classifier, incumbent architecture verbatim (500 rounds max, lr 0.05, 63 leaves, min_data_in_leaf 200, feature/bagging_fraction 0.85, bagging_freq 5, L1 0.1, L2 1.0, is_unbalance), early stop 30 on purged inner validation. Trained per fold on real features + execution-consistent labels.

**B. Fixed a-priori rule (interpretable floor).** *Not* distilled from the proxy-trained incumbent (provenance broken). Fixed before evaluation: trade when `realized_vol_30m` is in the top tercile of its trailing 60-session causal distribution AND `minute_of_day` falls in pre-registered windows; parameters chosen from 2020 data only (strictly prior to the earliest walk-forward test fold, 2021Q1, so no test fold's calendar range overlaps the calibration window), frozen at registration.

**C. One challenger (meaningfully distinct, not a hyperparameter costume).** LightGBM **Huber regression** predicting execution-consistent net R after costs: 200 trees, depth 2, 4 leaves, lr 0.03, min leaf 100, feature/bagging fraction 0.7, L1 1, L2 10. No tuning, no monotonic constraints. Trades when predicted net R > 0 subject to the same activity-rate threshold machinery.

**D. Baselines (activity-matched, causal):** time-of-day-only, volatility-only, unconditional-long, random-entry. Each matched separately to each candidate's fold-level executed trade count and eligibility windows. Random-entry: 1,000 replicates, seeds derived deterministically from `(protocol_version, candidate, fold, replicate)`; candidate compared against the median with the full null distribution retained.

**3.4 Attribution ladder (side track, non-promotable, outside selection):** frozen `.lgb` under (i) proxy inputs + legacy execution (reproduces 2.1676), (ii) proxy inputs + causal execution (2.0667 — already done), (iii) real inputs + causal execution (distribution-shift diagnostic). Stepwise deltas answer: model vs proxy vs look-ahead vs fills vs costs. Cheap (replay exists); runs after the freeze, cannot modify specs.

---

## 4. Validation design

**4a. Fold grid.** 18 expanding quarterly folds: train 2020-01→2020-12 / test 2021Q1, …, train →2025Q1 / test 2025Q2. Walk-forward ends 2025-06-30 (holdout claims the rest). COVID 2020 stays in training; reported as a regime slice. Within each fold, the last chronological 20% of training rows = inner validation for early stopping and threshold fitting, with a purged boundary.

**4b. Threshold rule (no percentile-of-day gating anywhere).** Absolute score threshold per fold, fitted on inner validation to hit **0.70 executed trades per full session** (measured after position-exclusivity and the 3/day cap): target count = `round_half_up(0.70 × sessions)`, threshold minimizing count error, ties toward fewer trades. Frozen for that fold's OOS quarter.

**4c. Replay state machine (pre-registered, closing R1 ambiguities):** no new entry while a position is open; simultaneous candidates ordered by score then earlier timestamp; entry bar itself eligible for barrier touches after entry; halt on **realized** PnL, effective from the next bar; timeout exit at bar close; expiry-day and roll handling explicit; 1.5× sizing only if integer-lot realizable, else 1.0× (rounding rule frozen). Eligibility unchanged: ≥09:45, <14:55, exclude 11:00–12:00, exclude compression regime.

**4d. Purging.** Any row whose 60-minute label window overlaps a train/validation/test boundary is purged — defined in timestamps, applied to fold boundaries, inner-validation split, and threshold-fitting samples alike.

**4e. Metrics.** Stitched strictly-OOS daily sleeve returns (bps on fixed sleeve capital, **including zero-trade days**) → annualized Sharpe ×√252; 20-trading-day moving-block bootstrap, 10,000 draws, 95% intervals (correctly labeled); score-quintile monotonicity; stability slices: vendor seam (GFDL→Groww 2024-10/11), lot-size eras, tax eras, expiry vs non-expiry weeks, volatility regimes (lagged VIX terciles — diagnostic only), time buckets; quarterly PnL concentration.

---

## 5. Costs, risk, sizing

- **Costs:** era-dated schedule from §1d applied per trade at actual notional; headline = base schedule (~2.9–3.4 bps RT); stress at **3.5 / 5 / 7 bps** flat RT (5 bps = autopsy's kill level).
- **Risk unit:** `R_t = 30 bps × entry notional`. Per-trade floor −1R (was −₹3,000 at lot 50); daily halt −5R (was −₹15,000). Removes rupee-semantics breakage across lot eras 50→25→75→65.
- **Execution:** date-specific integer lots; reporting in normalized sleeve-return bps so eras are comparable.

---

## 6. Gates & decision rules (all pre-registered)

**6a. Viability (absolute, hard — a candidate failing any is out):**
- net mean daily PnL > 0 at era-dated costs;
- WF Sharpe ≥ 0.75; holdout Sharpe ≥ 0.50 (normalized daily sleeve returns incl. zero-trade days);
- trades: ≥300 WF, ≥150 holdout, ≥40 per holdout half-year, ≥30 post-2026-04-01 (STT era);
- positive aggregate PnL and Sharpe at 5 bps stress;
- beats its activity-matched baselines (paired);
- no single quarter/era/time-bucket dominating profits;
- operationally executable (sizing, halts, data availability live).

Trade minimums are **hard fails** (140 holdout trades = fail, no post-hoc relaxation).

**6b. Selection among survivors:** paired daily-return comparison; winner needs ΔSharpe ≥ 0.25 in both WF and holdout AND paired moving-block-bootstrap 95% CI-low > 0 on mean daily PnL difference. No survivor → kill.

**6c. Kill criteria:** no deployable candidate passes 6a → **Sleeve F v0 dead**; keep the autopsy; effort moves elsewhere. "Insufficient sample" = not deployable = dead as production candidate (without claiming all future NIFTY routers impossible).

**6d. Multiplicity:** 7 registered selectable configs (A, B, C, D×4). Cumulative k = 88 prior + 7 = **95** for DSR (attribution runs excluded only because irrevocably non-promotable and spec-inert; if any attribution insight alters specs → k=98 and protocol v2). Every inspected variant of anything counts toward k. All results reported, none dropped.

**6e. Outcomes:** winner clears 6a+6b → **paper trade the exact frozen artifact** (no retrain on holdout — retraining would create an unevaluated model). Thin-positive → continue frozen paper measurement, no promotion. Nothing clears → kill per 6c.

---

## 7. Holdout

**2025-07-01 → 2026-06-30, untouched, opened exactly once** after WF selection is complete and frozen. ~250 sessions; spans lot eras 25→75→65 and the 2026-04 STT hike (post-STT segment reported separately, not required to be independently significant). Once opened, it is spent — the subsequent paper period becomes the true prospective test.

## 8. Shadow protocol (2026-07-01 onward)

Frozen winner runs in shadow with unavailable OI features = NaN, flagged **degraded-input, non-comparable**. OI-dependent winner is not excluded; clean-OI performance reported separately from the first date normal OI resumes. Groww live OI absence to be monitored; if OI returns via other endpoint, shadow re-baselined.

## 9. Sequencing (execution later, ~2–3 weeks of work)

1. **Week 1:** Phase 0 — label generators + parity test, real-feature matrix, era metadata table, exclusion policy. (Blocker gate: archive label parity must pass.)
2. **Week 1–2:** Phase 1 registration doc + hashed configs committed; runner verifies git-tracked status.
3. **Week 2–3:** Walk-forward execution (A, B, C, baselines), gates evaluated; attribution ladder in parallel (cheap).
4. **End week 3:** one-shot holdout, decision, paper-trade or kill. Report: all runs, all gates, deltas, DSR at k=95.

**Protect in order** (if schedule squeezes): data/label validation → written pre-registration → walk-forward → one-shot holdout → cost/risk + paired bootstrap. **Cut first:** extra diagnostic slices → presentation polish → candidate C. (Fable override of Sol's ranking: attribution ladder kept even under pressure — it's near-free with the existing replay and answers "where did 2.17 come from", which the campaign owes its history.)

---

## 10. User decisions (locked 2026-07-11)

1. **Holdout: 12 months (2025-07-01→2026-06-30).** DECIDED. Walk-forward ends 2025-06-30.
2. **Candidate C: kept.** DECIDED. Remains first on the cut list only if execution schedule forces it.
3. **Activity target: 0.70 executed trades per full session.** DECIDED.
4. **Sleeve capital figure: deferred.** Must be frozen at Phase 1 registration (any constant works; needed only to make normalized bps concrete). Placeholder until then.
