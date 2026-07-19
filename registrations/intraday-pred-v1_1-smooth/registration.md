# intraday-pred v1.1-smooth — frozen prospective registration

**Status:** frozen registration, no execution authorized until implementation is reviewed against this document.
**Registered:** 2026-07-19, per Sol verdict `registrations/intraday-pred-v1/verdict-2026-07-19.md` (design authority), under principal delegation of 2026-07-19.
**Scope:** one bounded secondary experiment combining (a) a forward-only smoothed direction target and (b) a prospectively frozen confidence-filter policy, on NIFTY index futures at H15 and H60. Nothing else. No magnitude output, no price output, no per-stock extension, no horizon additions.

## Hypothesis

The object worth predicting may be the direction of the underlying forward *path* (noise-smoothed) rather than the noisy endpoint return at exactly t+h. This changes the estimand; it does not reopen magnitude or price prediction (both retired for this information class).

## Smoothed direction target

For each horizon h, a forward-only EMA over the label window only:

- Q_0 = F_t; Q_j = α_h · F_{t+j} + (1 − α_h) · Q_{j−1}, j = 1…h
- Half-life: H15 = 5 minutes, H60 = 15 minutes; α_h = 1 − 2^(−1/half-life)
- Smoothed return: r_smooth = log(Q_h / F_t)
- Classify UP/FLAT/DOWN with the existing frozen volatility-scaled band (σ_h,t, b_h,t = max(0.0002, 0.25·σ_h,t)) unchanged.
- Validity: every class must be ≥ 15% of evaluation rows at that horizon, else the smoothed target is invalidated as excessively FLAT and the study fails structurally.

## Candidate ladder (frozen; no post-results substitution)

| Candidate | Features | Target | Role |
|---|---|---|---|
| S0 | frozen v0 42-feature list | raw endpoint | comparator |
| S1 | v0 + fixed causal EMA stack | raw endpoint | input-only diagnostic |
| S2 | frozen v0 42-feature list | smoothed | target-only diagnostic |
| S3 | v0 + fixed causal EMA stack | smoothed | **pre-committed production candidate** |

Fixed causal EMA stack (5 features, futures price only, same-session bars at or before decision t, EMAs reset each session, 45-minute warm-up required):

1. price-to-EMA gap, half-life 5
2. price-to-EMA gap, half-life 15
3. 5-minute slope of EMA(hl=5)
4. 5-minute slope of EMA(hl=15)
5. EMA(hl=5) − EMA(hl=15) spread

Models: the frozen v0 LightGBM classifier architecture, unchanged (350 estimators, lr 0.03, 31 leaves, min_child_samples 400, existing regularization/bagging/class weighting, seed 20260718, deterministic). No hyperparameter search. Calibration: session-weighted top-label correctness, isotonic with frozen Platt fallback, per horizon, calibration rows excluded from base training.

## Confidence-filter policy (prospectively frozen)

- Training: through 2025-03-24. Calibration + threshold fitting: 2025-04-01 → 2025-06-23 (embargoes 2025-03-25→31 and 2025-06-24→30, per v1 protocol).
- **Primary policy:** per-horizon confidence cutoff = calibration-set 75th percentile (intended top-25%).
- **Secondary policy:** calibration-set 90th percentile, report-only; may not replace the primary after evaluation.
- Actions: only predicted UP or DOWN; non-overlapping decisions; fixed next-bar entry, horizon exit, frozen costs/sizing/overlap handling (specified at implementation freeze, before first eligible observation).
- No policy selection from burned dev folds, 2025H1, or final results.

## Evaluation data

The five v1 development folds and 2025H1 are **burned for selection**; they may support implementation validation and clearly-labeled descriptive diagnostics only. 2025H2 remains **sealed**.

Primary evaluation: **prospectively accruing sessions**, beginning after this registration's implementation freeze; minimum **60 new trading sessions**, evaluated minute-pooled and non-overlapping. The evaluation window must be declared before the first eligible observation.

## Pass gates for S3 (all mandatory; non-compensating; per horizon unless stated)

Direction:
- smoothed-label balanced accuracy ≥ 0.400
- smoothed-label macro AUC ≥ 0.560
- raw endpoint-label balanced accuracy ≥ 0.370
- raw endpoint-label macro AUC ≥ 0.540
- raw endpoint BA improvement over S0 ≥ 0.003
- each raw-direction gate passes in both halves of the evaluation period

Confidence:
- ECE ≤ 0.030 (H15) / ≤ 0.050 (H60)
- decile-accuracy Spearman ≥ 0.80
- top-minus-bottom decile accuracy ≥ 0.08

Primary filter policy:
- filtered exact-class accuracy improvement over unfiltered actionable accuracy ≥ 0.030
- paired 2,000-draw session-bootstrap 95% lower bound for that improvement > 0
- realized coverage 15%–35%
- non-overlapping accepted observations ≥ 500 (H15) / ≥ 150 (H60)
- net mean return after frozen costs > 0, with session-bootstrap 95% lower bound > 0
- net annualized Sharpe ≥ 0.50

## What a pass licenses

One confirmatory sealed 2025H2 evaluation or shadow continuation — **not trading**. Live capital requires a separately registered shadow-to-live gate. Unsealing 2025H2 requires every mandatory gate above to pass; a monotone confidence curve, one strong horizon, or smoothed-label accuracy alone is insufficient.

## Kill criteria

Failure of any mandatory leg kills v1.1-smooth **without** an EMA sweep, half-life sweep, flat-band change, or target variation. Known transfer risks (recorded so a "pass" cannot be manufactured): smoothing can make labels easier while producing no endpoint or economic edge; can inflate FLAT prevalence; creates overlapping-label dependence; can fabricate accuracy by attenuating moves — hence raw endpoint performance is a mandatory co-gate.

After a kill, intraday-pred parks until a structurally new information class arrives (options-surface state, order-book data, verified event/news timing). Further transformations of the same futures/spot/BANKNIFTY/VIX price history do not qualify.

## Leakage prohibitions

- No centered or two-sided smoothing anywhere.
- No input may include any bar after decision timestamp t; external inputs remain restricted through t−1.
- The label EMA uses only t+1 … t+h; never beyond the horizon; no symmetric window around t+h.
- No smoothing across session boundaries.
- No post-results change to half-lives, flat bands, feature set, thresholds, or the production candidate.
