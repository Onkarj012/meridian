# Sleeve F — Campaign 2 conditional registration (both branches), k = 101

**Written and hashed during Campaign 1, before the C1 holdout is opened.** The branch that executes is determined solely by C1's frozen decision rules; the non-executed branch is never inspected and adds no trial.
**k ledger:** 95 (C1) + 2 candidates + 4 baselines = **101**. Any extra feature set, seed ensemble, horizon, activity target, threshold counterfactual, or model family = new registration + increment.

## 1. Thesis

The incumbent architecture's edge, if any, survives on a live-deployable information set — futures price/volume + calendar + lagged VIX only (no OI, no spot/basis) — with repaired semantics, a continuous net-R objective, and a causal activity policy. Secondary, answered empirically: does curation (C2-P) beat the repaired wide set (C2-W)?

## 2. Candidates

- **C2-W — repaired wide, 33 inputs** (k+1): ret_{1,5,15,30,60}m, log_ret_{1,5,15,30}m, natr_{5,15,30}m, realized_vol_{5,15,30}m, vwap_dev, vwap_slope_5m, or_dist_high/low, or_breakout_up/dn, minute_of_day, hour_of_day, session_progress, day_of_week, true_gap_pct, consec_bars (zero-reset), ema_slope, volume_surprise_60d, days_to_expiry, expiry_week, vix_t1_z252, vix_t1_return.
- **C2-P — path-core, 23 inputs** (k+1): vol-normalized ret_{5,15,30}m; realized_vol_{5,15,30}m; causally same-minute-standardized vwap_dev; vwap_slope_5m; opening_range_location; 60-session-standardized opening_range_width; trend_efficiency_{15,30}m; pullback_depth_15m; same-minute range_expansion_{15,30}m; true_gap_pct; gap_fill_fraction; session_time_sin/cos; days_to_expiry; expiry_week; vix_t1_z252; vix_t1_return.
- **Baselines (k+4):** time-of-day-only, volatility-only, unconditional-long, random-entry (1,000 deterministic replicates = one registered null family); C2 eligibility, activity-matched per candidate.

All standardizers/baselines: preceding 60 completed sessions, minute-of-day matched where applicable, ≥20 valid prior sessions else NaN. No outcome-based admission anywhere. Matrices built outcome-blind and hashed (`runs/sleeve-f-c2-matrices/hashes.json`; builder `features/c2_sets.py`, matrix + config hashes incorporated into this registration's freeze commit).

## 3. Shared label & replay

Decision at bar close, entry next-bar open; last decision bar 14:29; full 60-bar horizon; +40/−30 bps from actual entry; OHLC first-touch, stop wins double-touch; timeout at 60th-bar close; target = realized net R after era-dated costs (`policy/era_costs.py`, hash `5e1954ce…e74f`); fixed integer lots; no calibration/confidence/Kelly sizing; one position at a time, max 3/session; eligibility 09:45–14:29 excluding 11:00–12:00; **no compression-regime exclusion** (compression-condition diagnostic slice reported, never selected on); headline era costs + 3.5/5/7 bps stresses; day-balanced weights; day-blocked validation; 60-min purging.

## 4. Model & training mechanics (final, architecture doc §1)

Single shallow LightGBM Huber net-R regressor: objective=huber, **alpha=0.90**, 200 trees max, depth 2, 4 leaves, lr 0.03, min_data_in_leaf 100, **min_sum_hessian_in_leaf=1e-3**, feature/bagging fraction 0.70, bagging freq 5, L1 1.0, L2 10.0, expanded defaults committed, one protocol-derived seed (SHA-256 of `protocol_version|candidate_id|fold_id` → first 8 bytes big-endian → 1+(v mod 2147483646)), early stop 30 on **mean of per-session equally-weighted Huber losses** over purged chronological inner validation (last 20 % of complete training sessions).
Day-balanced weights w = 1/n_d, no rescale. **Per-leaf audit: artifact fails if any leaf has < 20 distinct sessions.** One-sided purge: remove training rows with `label_end ≥ B`; validation/test rows stay. Deployable artifact: median fold `best_iteration` (round-half-up), refit once on all registered pre-shadow data, frozen before any prospective observation.
Determinism two-gate: (1) same registered environment → identical prediction vector + canonical tree dump; (2) cross-environment → max |Δscore| ≤ 1e-10, |Δthreshold| ≤ 1e-10, identical decisions — tolerance never excuses a decision mismatch. Full artifact bundle with golden fixtures per §1.5.

## 5. Activity controller (fully pinned)

Before each session D: last 60 completed **admissible** sessions strictly before D (frozen admission rule; excludes incomplete/corrupted/halted-without-counterfactual/boundary-contaminated); score with applicable frozen artifact; replay exact state machine at every distinct historical threshold; pick threshold minimizing |executed − 42|; ties → fewer trades, then higher threshold; frozen for session D. Fold init: same rule against last 60 admissible **training** sessions before the OOS boundary. < 60 admissible ⇒ fold invalid, no fallback. No gain, no smoothing, no intraday update. The fold-frozen-threshold counterfactual is not run (inspecting it = k+2).

## 6. Conditional decision tree (pre-committed)

- **Branch A — C1 yields a deployable winner:** C2 candidate must clear all absolute gates AND beat that exact frozen C1 winner prospectively: ΔSharpe ≥ 0.25 with paired moving-block-bootstrap 95 % CI-low > 0 on mean daily PnL difference, on the shared shadow period.
- **Branch B — C1 yields none (incl. thin-positive):** C2 evaluated fresh-thesis against absolute gates + registered baselines (activity-matched, paired).
- The unexecuted branch is never inspected.

## 7. Evidence rules & promotion

- C2 historical WF = development evidence only. C1's holdout window (2025-07→2026-06) is permanently spent for C2 claims.
- Both artifacts (C2-W, C2-P) shadow concurrently at 0.70/session from artifact freeze. Promotion requires **≥150 forward shadow trades** (~214 sessions ≈ 10 months), positive economics at 5 bps stress, concentration + operational gates.
- Sequential boundaries permit **early kill only** (operational failure, 5-bps economic failure under the registered sequential boundary, persistent activity shortfall) — never early promotion.
- Activity band: derived via `policy/activity_band.py` (config hash `e9df6ea7…48e5`) over all historical WF OOS sessions, pooled across both candidates, floor/ceil = 0.5th/99.5th pctile of rolling-60 executed counts; band + ledgers hashed at artifact freeze; never recomputed from live.
- Monitoring per the frozen alarm table (architecture doc §3.4); policy return is the only promotion/kill series.

## 8. Post-C2 sequencing (context, not part of this registration)

C3 = one frozen family at k=102 selected by the Six-Week Gate Cascade (provenance/coverage/latency/licensing/live-parity only, never PnL), clock starting at C2 artifact freeze. Confidence family (binary P(net R > 0) ranking functional) = k=103, opens only after a C2 candidate completes ≥150-trade forward qualification unkilled.
