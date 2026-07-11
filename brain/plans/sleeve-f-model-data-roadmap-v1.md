# Sleeve F — Model Training & Data Modelling Roadmap v1

**Date:** 2026-07-11
**Status:** PLANNING ONLY — nothing executed, no code changed. Feeds (i) minimal pre-freeze clarifications to `sleeve-f-training-protocol-v1.md` and (ii) a future Campaign 2 registration.
**Authors:** Claude Fable 5 × gpt-5.6-sol — initial draft + two adversarial passes (Sol R1 critique → Fable rebuttal/counter-challenges → Sol R2 final). Debate transcripts in session scratchpad (`fable_draft_v0.md`, `sol_round1.md`, `fable_rebuttal_r1.md`, `sol_round2.md`).
**Relationship to protocol v1:** Campaign 1 (protocol v1) runs verbatim at k=95. This document changes nothing about its candidates, gates, or holdout. It adds only defect-correcting clarifications legal before the Phase-1 freeze (§2), and plans what comes after.

---

## 0. The one fact that governs everything

The deployable policy executes 0.70 trades per session. That means:

- ~1,600 archive sessions → ~1,120 lifetime executed trades;
- stitched 2021Q1–2025Q2 walk-forward → **~770 OOS trades**;
- 12-month holdout → **~170 trades**;
- two quarters of shadow → ~88 trades (below Campaign 1's own 150-trade floor).

Selection power is bounded by policy trades (~10³), not by the ~400k eligible minute rows (which do support model *training* — roughly 7,000–10,000 non-overlapping 60-min path episodes). Consequence: **capacity for training is adequate; capacity for choosing among variants is scarce.** Every plan below rations variants, not features or rows.

Break-even arithmetic (+40/−30 bps barriers, ignoring timeouts): win rate must exceed 47.1% at 3 bps RT cost, 50.0% at 5 bps (the kill stress). The model's job is to improve expected net R among mandatory trades, not classification accuracy in the abstract.

## 1. Debate resolution log (what was proposed, what survived, why)

| Proposal (Fable draft) | Verdict | Reason |
|---|---|---|
| Deep sequence models (LSTM/TCN/transformer) as a registered slot | **Killed** (both agree) | ~770 OOS trades cannot referee tuned deep models; overlapping paths fake sample size; seam/COVID memorization risk |
| Two-head p(win)×E[R] | **Killed** | Formula malformed — double-counts win probability; correct decomposition needs 3 probabilities + conditional magnitudes the sample can't estimate |
| Meta-labeling (Prado) on a rule primary | **Killed** | Locks opportunity set to a crude rule; may make 0.70/session infeasible; secondary can't recover unnominated trades |
| Isotonic calibration layer | **Killed** | Small serially-dependent inner val; policy is rank-based at forced activity — calibration buys nothing without probability-based sizing (also killed) |
| Fractional Kelly sizing | **Killed** | ~170 holdout trades cannot support it; magnifies estimation error. Fixed integer-lot sizing stands |
| Regime-conditional thresholds | **Killed** | Triples threshold-estimation burden; inner-val quarters lack trades for regime-specific activity targeting |
| Seed-bagged ensemble | **Killed for C2** | Not free — seed count/aggregation are hyperparameters; would be one more registered config. Single protocol-derived seed; seed-sensitivity accepted as an unmeasured risk (measuring it would itself burn k) |
| Per-feature admission rule (IC sign stability) | **Killed** | Internally contradictory (needs years, pre-2021 has one); univariate IC rejects interactions; screening survivors = uncounted multiplicity. Replaced by frozen family-level sets |
| Noise-feature canary | **Killed** | Tree importance biased by cardinality/correlation; noise beating median real feature is expected with redundant sets |
| 8–12 feature cap (Sol R1) | **Killed** (Sol yielded) | Hand-curation is its own uncounted prior; depth-2/4-leaf/200-tree ≈ ~600 splits — 25–40 frozen inputs not intrinsically excessive. Resolved empirically: two frozen sets race (§3) |
| Additive/logistic architecture control (Sol R1) | **Killed** (Sol withdrew) | Second slot better spent on the wide-vs-curated feature contrast, which is decision-relevant |
| Revisit 0.70 activity target in C2 | **Rejected** | 0.70→0.50 saves ~0.64 bps/session cost drag but stretches the 150-trade forward gate from ~214 to ~300 sessions; no evidence either dominates; continuity with C1 wins |
| Options surface features (PCR/IV/GEX) in C2 | **Deferred** | Archives unvalidated, seam CSV↔parquet, snapshot timestamps unproven (same-day EOD = look-ahead), live continuation unknown. Data-contract audit first; candidate slot only in a later campaign with its own thesis |
| Breadth (nifty500 minute) | **Deferred** | Survivorship (current-constituent archive), corporate actions, volume semantics. Blocked on historical constituent lists |
| BankNifty (features or pooled training) | **Deferred** | 93 expiry-week OI drift fails, spot ends 2024-10, high correlation with NIFTY (no free independent bets, correlated exactly in stress) |
| Intraday spot/basis features in C2 | **Deferred** (Fable yielded) | Live spot provenance unproven (July files exist but weren't written by the top-up); same live-parity logic that excludes OI |
| OI features in C2 | **Excluded** | Groww live OI absent (all 8 July sessions zero). C2 is OI-free from inception — no model/fallback pair, no switching rule. OI re-enters only after ≥60 consecutive live sessions of nonzero unit-consistent OI + its own registered increment |
| Barrier-geometry search | **Deferred, flagged dangerous** | Geometry search is policy optimization; every inspected geometry increments k; post-C1 the holdout can't validate a new geometry — only forward time can |
| Bronze/silver/gold feature store | **Kept, demoted** | Useful engineering, but *after* a point-in-time semantic contract (units, provenance, raw-vs-corrected OI, cumulative-vs-differenced volume) and live/backtest parity tests. Hashing a wrong matrix makes the error reproducible, not correct |
| Causal activity controller (Sol) | **Kept, fully pinned** | Sol owned the hidden-hyperparameter charge and froze every constant (§3, controller spec). One registered policy per candidate; the fold-frozen counterfactual is NOT run (inspecting it = k+2) |
| Conditional pre-registration of C2 (Fable) | **Accepted by Sol** | Both branches written before C1's holdout opens; specs don't depend on C1 outcomes; only comparator/promotion rule branches. Saves ~1–2 months of critical path |
| Day-balanced training weights | **Adopted** | Each session's row-weights sum to 1 — bounds any session's influence; safer than uniqueness weights / decay / non-overlap subsampling (all rejected: hidden hyperparameters or data waste) |

**Incumbent defects found during debate (Sol R1, verified against `features/sleeve_f_router.py`):**
1. `gap_pct` ≡ 0 always — per-day frames make `prev_close.iloc[0]` NaN, code substitutes same-day open. The incumbent never saw an overnight gap.
2. `add_regime()` is noncausal — quantiles computed over the passed frame (future data). Since eligibility excludes the compression regime, this is an **eligibility leak into Campaign 1** unless fixed (§2).
3. OI buildup quadrants mislabel zero-change bars as bearish/unwind.
4. Late-session labels are heterogeneous: entries to 14:54 with a "60-min" horizon that can't exist after ~14:30 — `minute_of_day` lets the model exploit the artifact.

## 2. Campaign 1 pre-freeze clarifications (endorsed by both; k stays 95)

Legal because v1 is approved but not frozen and no outcome data has been touched; these are defect corrections of already-approved requirements, not outcome-selected variants.

1. **Causal regime construction** for deployable candidates: regime quantiles from the preceding 60 completed sessions, realized-vol observations matched by minute of day, ≥20 valid prior sessions required (else row ineligible), no current/future-session data. Incumbent-exact noncausal regime survives only inside the non-promotable attribution/parity track. Running and comparing two regime definitions would void the exemption (each inspected alternative → k+1, protocol v2).
2. **Late-entry honesty:** keep `<14:55` eligibility, but define timeout as `min(60 bars, last same-session bar)` and report decisions ≤14:29 vs ≥14:30 separately.
3. **Point-in-time boundary stated explicitly:** features from data through decision-bar close; entry next-bar open.
4. **NaN policy enumerated:** which features reach LightGBM as NaN vs which rows become ineligible; no cross-session forward-fill.
5. **Hash-pin the causal regime builder + tests** alongside the feature matrix.

## 3. Campaign 2 — conditional registration outline (write during C1, run after)

**Thesis:** the incumbent architecture's edge, if any, survives on a live-deployable information set (futures price/volume + calendar + lagged VIX only) with repaired semantics, a continuous net-R objective, and a causal activity policy. Secondary question, answered empirically: does curation beat the repaired wide set?

**Shared label & replay:** decision at bar close, entry next-bar open; last decision bar 14:29; full 60-bar horizon; +40/−30 bps from actual entry; OHLC first-touch, stop wins double-touch; timeout at 60th-bar close; target = realized net R after era-dated costs; fixed integer lots; no calibration/confidence/Kelly sizing; one position at a time, max 3/session; eligibility 09:45–14:29 excluding 11:00–12:00; **no compression-regime exclusion** (explicit difference from C1 — the regime filter was a noncausal incumbent artifact; a compression-condition diagnostic slice is reported, not selected on); headline era-dated costs + 3.5/5/7 bps stresses; day-balanced weights; day-blocked validation, 60-min purging.

**Shared model:** LightGBM Huber regression — 200 trees max, depth 2, 4 leaves, lr 0.03, min leaf 100, feature/bagging fraction 0.70, bagging freq 5, L1 1.0, L2 10.0, one protocol-derived seed, early stop 30 on purged chronological inner validation. No tuning, no bagging. Final prospective artifact: median WF `best_iteration` (round-half-up), refit once on all registered pre-shadow data — legal here (unlike v1 §6e) because C2's real test is the shadow period, which evaluates exactly that refit artifact.

**Activity policy (fully pinned causal controller):** before each session, take the preceding 60 completed full sessions, score with the frozen fold model, replay the exact C2 state machine at every distinct historical score threshold, pick the threshold minimizing |executed − 42| (= round_half_up(0.70×60)); ties → fewer trades, then higher threshold; apply unchanged for the next session; WF folds initialize from the final 60 purged inner-validation sessions; <60 valid init sessions → fold invalid (no fallback); no gain/smoothing/intraday update. Rationale for 60: binomial SE of activity rate = √(0.7·0.3/60) ≈ 0.059 (8.4% of target) vs 14.6% at 20 sessions vs halved responsiveness at 120. The fold-frozen-threshold counterfactual is not run; inspecting it costs k+2.

**Candidates:**
- **C2-W, repaired wide set (k+1), 33 inputs:** incumbent futures-only variables repaired — `ret_{1,5,15,30,60}m`, `log_ret_{1,5,15,30}m`, `natr_{5,15,30}m` (ATR/price), `realized_vol_{5,15,30}m`, `vwap_dev`, `vwap_slope_5m`, `or_dist_high/low`, `or_breakout_up/dn`, `minute_of_day`, `hour_of_day`, `session_progress`, `day_of_week`, `true_gap_pct` (real prior-session close), `consec_bars` (zero-return resets to 0), `ema_slope`, `volume_surprise_60d` (same-minute baseline over preceding 60 sessions), `days_to_expiry`, `expiry_week`, `vix_t1_z252`, `vix_t1_return`. No OI, no spot/basis.
- **C2-P, path-core set (k+1), 23 inputs:** vol-normalized `ret_{5,15,30}m`; `realized_vol_{5,15,30}m`; causally same-minute-standardized `vwap_dev`; `vwap_slope_5m`; `opening_range_location`; 60-session-standardized `opening_range_width`; `trend_efficiency_{15,30}m`; `pullback_depth_15m`; same-minute `range_expansion_{15,30}m`; `true_gap_pct`; `gap_fill_fraction`; `session_time_sin/cos`; `days_to_expiry`; `expiry_week`; `vix_t1_z252`; `vix_t1_return`. All standardizers: preceding 60 completed sessions only, matched by minute of day where applicable. No outcome-based admission anywhere.
- **Baselines (k+4):** time-of-day-only, volatility-only, unconditional-long, random-entry (1,000 deterministic replicates = one registered null family); C2 eligibility, activity-matched per candidate.

**Ledger: k = 95 + 2 + 4 = 101.** Any extra feature set, seed ensemble, horizon, activity target, threshold counterfactual, or model family = new registration + increment.

**Conditional decision tree (pre-committed before C1's holdout opens):**
- C1 yields a deployable winner → C2 must clear absolute gates AND beat that exact frozen winner prospectively (ΔSharpe ≥ 0.25, paired block-bootstrap CI-low > 0 on mean daily PnL diff).
- C1 yields none (incl. thin-positive) → C2 evaluated fresh-thesis against absolute gates + registered baselines.
- The non-executed branch is never inspected; adds no trial.

**Promotion evidence:** historical WF is development evidence only (C1's holdout period is permanently spent for C2). Promotion requires ≥150 forward shadow trades (~214 sessions ≈ 10 months at 0.70/session), positive economics at 5 bps stress, concentration + operational gates. Sequential boundaries permit early **kill** only (operational failure, 5-bps economic failure, persistent activity shortfall) — never early promotion.

## 4. Parallel outcome-blind workstreams (burn no k; start during C1)

- Causal regime builder + tests (also serves C1 clarification §2.1).
- Semantic repairs: `true_gap_pct`, NATR, zero-change semantics, same-minute volume baselines.
- Historical session-quality contract for 2020–2023 (validation rigor currently concentrated post-2024; odd bar counts — 60/106/221/317/334/372/750 — need per-session dispositions).
- Expiry-calendar and days-to-expiry join validation; VIX dedup (3 duplicate dates) + T−1 availability proof.
- Build both C2 feature matrices **without examining label association or PnL**; hash them.
- Offline/live feature-parity harness; live monitoring of futures volume/OI/spot availability (OI's 60-session clean streak clock starts when nonzero OI resumes).
- Options-archive audit (timestamps, snapshot frequency, strike/expiry mapping, CSV↔parquet seam, live continuation) — infrastructure only, no features.
- Historical index-constituent sourcing (unblocks breadth later).
- Point-in-time semantic contract (units/provenance per field) → then versioned bronze/silver/gold store + append-only experiment (k) ledger.
- Label/replay test suite incl. 14:30 boundary and double-touch.

**Not free (burns research capital if it influences a selectable spec):** adversarial-validation-driven feature removal, feature ICs, geometry comparisons, slippage assumptions that alter policy, any outcome diagnostic used to choose between specs.

## 5. Timeline

| When | What |
|---|---|
| Weeks 0–1 | Apply §2 clarifications; freeze C1 at k=95; start outcome-blind workstreams |
| Weeks 1–3 | Execute C1 walk-forward + attribution ladder; in parallel write, hash, and commit C2's complete conditional registration (both branches, k=101 ledger). No C2 outcome runs |
| End week 3 | Open C1 holdout once; apply frozen decision rules; activate the matching pre-registered C2 branch |
| Weeks 4–6 | C2 historical WF (development evidence); train + freeze final C2-W and C2-P artifacts before any prospective observation |
| Months ~2–12 | Shadow C2-W and C2-P concurrently at 0.70/session; ≥150 trades ≈ 10 months; early-kill boundaries active; no early promotion |
| After | Prospective decision per §3. Only then consider ONE data-expansion campaign (options OR breadth OR BankNifty OR spot/basis OR OI) — after its data contract, live-availability proof, own thesis, own k |

## 6. Risk register (accepted, eyes open)

1. **Single-seed variance** — measuring seed sensitivity would itself burn k; accepted as unmeasured. If a future campaign registers an ensemble, it is one config, pre-specified.
2. **C1 winner may be operationally stranded** — candidate A uses OI + spot features while live OI is absent; v1's own operational-executability gate (6a) and degraded-input shadow (§8) adjudicate this. C2's OI/spot-free design is the hedge.
3. **Controller drift-contamination** — abrupt score drift (vendor/tax seam) can pollute the trailing 60-session window for up to 60 sessions; accepted vs the quarterly-staleness failure of fold-frozen thresholds; PnL superiority unproven by design (testing both = k+2).
4. **Timeline honesty** — no C2 promotion before roughly mid-2027. Forward trades are the only honest OOS left once C1's holdout is spent; nothing manufactures more of them.
5. **Groww dependency** — live shadow needs the ₹499/mo subscription (or replacement live feed) throughout; cancel-decision interacts with the shadow plan.
6. **Fill realism unmodeled** — 1-min OHLC can't model queue/spread; limit-entry savings treated as unavailable until quote/tick data exists; cost stresses are the only bracket.

## 7. Deferred-family ledger (each needs: data contract → live proof → thesis → own registration)

| Family | Blocker | First step (outcome-blind) |
|---|---|---|
| OI structure | Live OI absent | 60-session clean-streak monitor |
| Spot/basis (carry-adjusted) | Live spot provenance; contract mapping pre-2024 | Spot parity + contract-identity audit |
| Options surface | Unvalidated archives, timestamp/seam/continuation unknown | Schema/timestamp audit |
| Breadth | Survivorship; constituent history | Source historical constituents |
| BankNifty (features/pool/sleeve) | OI drift fails, spot ends 2024-10, correlation | Quality gate + price-only lead-lag audit |
| Short side | Separate thesis; STT on sell leg; long-biased drift | Thesis memo only |
| Barrier geometry | Policy optimization; k-expensive; no holdout left | MFE/MAE diagnostics on C2 shadow trades (reported, not selected on) |
