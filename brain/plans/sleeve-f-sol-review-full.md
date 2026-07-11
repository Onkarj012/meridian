> **Bottom line:** the new rule grids are dead. The original OptiNet LightGBM router has **not** been revalidated because it was never ported. Worse, its original “forward-walk” contains a future-aware daily percentile threshold, so Sharpe 2.17 should be downgraded from “validated edge” to “unresolved claim.” Do not promote Sleeve F, but do not declare the original model disproven yet.

## 1. Plan review

### What the design gets right

The document has the right research culture:

- Promotion rather than assumption.
- Real instruments rather than proxies.
- Full costs, point-in-time features, baselines, pre-registration, sealed tests, multiple-testing control, and retained autopsies.
- Separation of predictive accuracy from trading economics.
- Explicit willingness to kill Sleeve F if the real-feed evidence fails.
- Good operational discipline: ledgers, halts, versioning, paper/live divergence.

Those are strong foundations. The language around CI-low, selection bias, and keeping negative results is especially sound.

### Internal inconsistencies and blind spots

1. **Sleeve F is called promoted before passing its own prerequisite.**

   The document says to port it “as-is” as the first promoted sleeve, but later says real-feed walk-forward is required before promotion. It should be an inherited candidate under quarantine, not promoted. See [UNIFIED_SYSTEM_DESIGN.md](/Users/onkarj012/Projects/market/meridian/brain/docs/UNIFIED_SYSTEM_DESIGN.md:33) versus the Phase 2 gate at [line 374](/Users/onkarj012/Projects/market/meridian/brain/docs/UNIFIED_SYSTEM_DESIGN.md:374).

2. **The concentration rule is ambiguous, but not quite as contradictory as it first appears.**

   The general P3 wording sounds universal, while §5.6 explicitly says the 20-symbol/20%-share gate applies to “equity sleeves” [here](/Users/onkarj012/Projects/market/meridian/brain/docs/UNIFIED_SYSTEM_DESIGN.md:215). The implementation consequently removes symbol concentration from the futures profile [here](/Users/onkarj012/Projects/market/meridian/evidence/sleeves.py:97).

   That is sensible, but the design needs an explicit gate-applicability matrix. For Sleeve F, replace symbol concentration with relevant concentration controls: percentage of P&L from one month, expiry week, time-of-day bucket, regime, and trading day.

3. **“One identical gauntlet” does not fit all sleeves.**

   Cross-sectional stock splits and 20-symbol gates make no sense for a single-index futures time series. Conversely, a 10-day stock embargo is insufficiently specific for overlapping 120–180-minute futures labels. Each sleeve needs a shared core plus a sleeve-specific validation appendix.

4. **P1 is too simplistic.**

   “Natural target hit rate versus binary breakeven” is useful screening, but it is not a sufficient label audit when:

   - timeouts exit at a continuous return;
   - a model selectively enriches the base rate;
   - sizing varies by confidence;
   - target and stop events are not exhaustive.

   The original router made most of its reported money from time exits, not targets. A correct viability equation must include target, stop, and timeout probabilities and conditional payoffs.

5. **The worst-fold rules are internally redundant.**

   The implementation requires positive share of folds and `worst_fold_bps >= 0` [here](/Users/onkarj012/Projects/market/meridian/evidence/sleeves.py:25). If the worst fold must be non-negative, then effectively 100% of folds must be non-negative; the 60% or 70% positive-fold gate adds nothing. Define a tolerable negative floor, or remove one of the gates.

6. **The sealed-test governance is aspirational, not enforced.**

   The doc says grids must be committed before running and the seal is spendable once [here](/Users/onkarj012/Projects/market/meridian/brain/docs/UNIFIED_SYSTEM_DESIGN.md:315). The current runner simply writes `committed_grid: true` [here](/Users/onkarj012/Projects/market/meridian/scripts/run_optinet_sleeve_f_full.py:212). It does not verify git state, config hash, or seal status.

7. **Portfolio-level risk is missing.**

   Independently promoted sleeves can still load the same underlying NIFTY beta, volatility regime, or crash exposure. The unified design needs:

   - cross-sleeve return and tail correlation;
   - aggregate margin and gap-risk limits;
   - capital-allocation rules;
   - a portfolio-level kill switch.

8. **Futures-specific mechanics are under-specified.**

   Missing or weakly specified: contract rollover construction, expiry-week behavior, historical lot-size changes, margin usage, mark-to-market cash flows, roll gaps, price-limit behavior, and date-versioned transaction taxes.

9. **“ECE ≤ 0.10” is not recommendation accuracy.**

   Calibration is useful, but ECE alone can look acceptable for a low-skill model. It should be paired with Brier score/log loss, calibration slope/intercept, coverage, and realized economic value by probability bucket.

## 2. Progress review

### Plain verdict

| Question | Verdict |
|---|---|
| Do the new breakout/momentum/VWAP/OR/basis rules have edge? | **No. Kill them.** |
| Has the original OptiNet LightGBM edge failed on real futures? | **Not tested.** |
| Was the original model ported? | **No. It was not ported at all.** |
| Is the original Sharpe 2.17 clean enough to trust unchanged? | **No. The original selection policy has look-ahead.** |
| Is the real futures dataset useful? | **Yes, but incomplete and not yet contract-audited strongly enough.** |

### These are not the original router

The original system:

- loads `final_long.lgb`;
- uses 39 features;
- is long-only;
- uses 40 bps target / 30 bps stop / 60 minutes;
- selects scores above the daily 85th percentile;
- takes at most three trades per day;
- sizes the top 5% at 1.5×.

That policy is visible in the original [forward_walk.py](/Users/onkarj012/Projects/market/intranet_optinet/scripts/research/forward_walk.py:39) and its scoring path [here](/Users/onkarj012/Projects/market/intranet_optinet/scripts/research/forward_walk.py:451).

The Meridian branch:

- never imports LightGBM;
- never loads either the locked single-tree or multi-tree model;
- does not compute the original 39-feature vector;
- fires handcrafted breakout, momentum, VWAP, opening-range, and basis rules [here](/Users/onkarj012/Projects/market/meridian/features/sleeve_f_signals.py:118);
- uses different entry, exit, overlap, cost, and trade-frequency rules.

Even the original 40/30/60 geometry is absent. The nearest Phase 2 cell is 40/25/60.

**Therefore the statement “the router edge does not survive real futures” is unsupported. The defensible statement is: “none of the newly invented rule families survives realistic costs on this real-futures sample.”**

### The negative rule results are nevertheless credible

The replay is reasonably conservative:

- next-bar-open entry;
- OHLC first-touch;
- stop wins same-bar ambiguity;
- same-day horizon;
- non-overlapping trades by default;
- fixed 3.5 bps cost;
- real futures OHLCV/OI.

The data contains 466,067 rows. Only 48 rows have zero OI and 666 have zero volume, so this is not another zeroed proxy dataset. The broad negative results are too large and consistent to explain away through bootstrap details:

- Phase 2, 36 configurations: every net EV negative, range **−4.08 to −1.96 bps/trade**.
- Extended, 24 configurations: 8 positive point estimates, but **zero positive CI-low**. Best point estimate: **+2.35 bps**, CI-low **−1.73**.
- Phase 3, 28 configurations: every net EV negative, range **−3.72 to −2.22 bps/trade**.

The reported “about 15% hit rate” should not be the headline. Those are sparse daily folds. Aggregate net-win rates are materially higher:

- Phase 2: 33.1–43.5%.
- Extended: 37.4–48.2%.
- Phase 3: 44.8–49.5%.

The real headline is that gross EV is generally near zero and the 3.5 bps friction overwhelms it.

### Material methodology defects

1. **The original evidence has look-ahead.**

   The original router calculates each day’s 85th and 95th score percentiles using the entire day, then applies those thresholds to earlier minutes [here](/Users/onkarj012/Projects/market/intranet_optinet/scripts/research/forward_walk.py:471). At 10:00, the afternoon score distribution is unknowable.

   That is not a causal forward test. The model features may be causal, but its trade-selection threshold is not.

2. **The current work is not walk-forward.**

   It is an 80% chronological selection sample plus a 20% holdout. No model is retrained, and no rolling selection/OOS folds are run. Weekly performance buckets are not walk-forward folds.

3. **The real data has a major gap.**

   Available days are:

   - 2020: 252
   - 2021: 248
   - 2022: 248
   - 2023: 246
   - 2024: 209
   - 2025: **0**
   - 2026: 43

   The nominal “sealed” period runs from January 2024 to June 2026, but is actually January–October 2024 plus roughly April–June 2026. It cannot reproduce the original November 2024–May 2026 window on real data.

4. **The seal is already contaminated.**

   The same holdout was used for Phase 2, extended, and Phase 3 searches. The later grids were conceived after earlier sealed outcomes were known. It is no longer sealed.

5. **At least the extended and Phase 3 grids were not pre-registered.**

   Both config files remain untracked, while reports claim `committed_grid: true`. The runner does not verify the claim.

6. **The confidence interval is mislabeled.**

   `lower_quantile=0.05` returns the 5th and 95th percentiles: a **90% two-sided interval**, not 95%. It uses 1,000 IID-resampled trading-day clusters [here](/Users/onkarj012/Projects/market/meridian/evidence/stats.py:33), whereas the design requires 20-day blocks and 10,000 draws.

7. **DSR does not use the global experiment count.**

   The code uses only the current grid size—36, 24, or 28—not the cumulative number of configurations ever inspected. At minimum the current visible count is 88, before failed attempts and inherited OptiNet searches.

8. **Baselines are not actually evaluated.**

   The futures promotion gate omits the rung-0 baseline gate entirely. “Zero beat baselines” currently means no evidence was supplied, not that a paired baseline comparison was run.

9. **The sealed pass rule is too weak in sample size and too strong in fold perfection.**

   It accepts any positive number of sealed trades, but requires the worst weekly EV to exceed zero [here](/Users/onkarj012/Projects/market/meridian/scripts/run_optinet_sleeve_f_full.py:388). The extended selected candidate had only 31 sealed trades—far too few—while one losing small week is enough to fail.

10. **“Real feed validated” is partly asserted through fabricated metadata.**

    Normalization supplies a deterministic token, `FUTIDX`, and `continuous_front_month` expiry [here](/Users/onkarj012/Projects/market/meridian/ingest/optinet_data.py:137). The underlying values look like genuine futures, but the validator does not prove contract identity, front-month selection, or roll correctness.

## 3. Label economics

For a binary target/stop trade with target \(T\), stop \(S\), and round-trip cost \(C\):

\[
EV = pT-(1-p)S-C
\]

\[
p_{\text{BE}}=\frac{S+C}{T+S}
\]

Using \(C=3.5\) bps:

| Geometry | Binary breakeven | Observed target-touch rate | Gross EV range | Verdict |
|---|---:|---:|---:|---|
| 12 / 8 / 10 | **57.50%** | 23.2–30.3% | −0.32 to +0.15 bps | Doomed |
| 16 / 10 / 15 | **51.92%** | 22.5–30.7% | −0.51 to +0.24 | Doomed |
| 20 / 12 / 20 | **48.44%** | 22.8–30.1% | −0.58 to +0.28 | Doomed |
| 25 / 15 / 30 | **46.25%** | 19.8–28.5% | −0.30 to +0.53 | Doomed |
| 32 / 20 / 45 | **45.19%** | 21.9–29.9% | −0.50 to +0.83 | Doomed |
| 40 / 25 / 60 | **43.85%** | 23.8–30.4% | −0.25 to +1.54 | Cannot cover cost |
| 50 / 30 / 90 | **41.88%** | 31.4–38.8% | −0.63 to +3.32 | Still no positive net cell |
| 65 / 40 / 120 | **41.43%** | 26.4–37.1% | −1.09 to +5.85 | Some timeout-assisted positive cells |
| 80 / 50 / 180 | **41.15%** | 22.7–34.1% | −2.50 to +5.54 | Same; not statistically established |
| 4 / 5 / 5 | **94.44%** | 44.9–45.8% | Negative | Absurd geometry after cost |
| 12 / 12 / 30 | **64.58%** | 41.6–45.7% | +0.10 to +0.34 | Doomed after cost |
| 35 / 35 / 60 | **55.00%** | 18.8–28.2% | +0.81 to +1.28 | Doomed after cost |
| 45 / 45 / 120 | **53.89%** | 21.3–28.4% | +0.71 to +0.82 | Doomed after cost |

For the illustrative 12/8 geometry:

- At a 35% binary hit rate:

\[
0.35(12)-0.65(8)-3.5=-4.5\text{ bps/trade}
\]

- At the reported painful 15% daily hit rate:

\[
0.15(12)-0.85(8)-3.5=-8.5\text{ bps/trade}
\]

There was no plausible rescue through training for those short geometries without extreme signal enrichment.

One terminology warning: `hit_rate` in the current summary means **net-profitable trades**, including profitable timeouts. The proper barrier-label statistic is `touch_rate`, meaning target touches. Conflating the two hides the actual label economics.

### The original router geometry

The original used 40/30/60, which this branch never tested.

- At 3.5 bps cost, binary breakeven is:

\[
\frac{30+3.5}{40+30}=47.86\%
\]

- At the original ₹105 cost on approximately ₹1 million one-side notional—about 1.05 bps—the binary breakeven is approximately:

\[
\frac{30+1.05}{70}=44.36\%
\]

But the original 1,128-trade report had only 98 targets, 155 stops, and 875 time exits. Target share among resolved target/stop trades was 38.7%, while time exits reportedly contributed about ₹313k. Therefore binary P1 alone would misdescribe that strategy. Its audit must use:

\[
EV=p_TT-p_SS+p_{TO}E[R_{TO}]-C
\]

## 4. Forward plan: 2–6 weeks

### Week 1: stop the search and write the correct autopsy

- Kill further breakout/momentum/VWAP/OR/basis grids.
- Reclassify all three runs as “rule-family autopsies,” not Sleeve F router validation.
- Mark the current holdout as spent.
- Record cumulative experiment count of at least 88.
- Remove “committed grid” and “sealed” claims where they are not true.
- Do not nominate the +2.35 bps extended cell as a champion.

### Weeks 1–2: port the incumbent exactly

Port and hash:

- locked `final_long.lgb`;
- all 39 original feature formulas;
- 40/30/60 geometry;
- long-only policy;
- entry-time exclusions;
- compression filter;
- max three trades/day;
- confidence sizing;
- daily halt behavior.

First reproduce the original proxy ledger as closely as possible. If the port cannot reproduce the 1,128 trades and ₹429k within a predeclared tolerance, stop: there is no valid experimental bridge.

Run two policy versions:

1. **Legacy parity:** full-day percentile, same-close/close-touch behavior—only to explain the old result.
2. **Causal candidate:** threshold determined exclusively from training history or expanding prior-day scores, next-bar-open entry, OHLC first-touch, and no future-aware halt state.

The legacy version is not promotable.

### Weeks 2–3: repair the data contract

- Acquire actual front-month futures for the missing 2025 window. If unavailable, say plainly that the original OOS period cannot be revalidated.
- Store actual contract token and expiry rather than synthesizing them.
- Publish roll rules and roll-day diagnostics.
- Verify price continuity, OI continuity, volume, duplicate minutes, session gaps, and spot/futures basis.
- Version lot size and all taxes by date.
- Convert the old ₹105 cost into per-trade bps using contemporaneous notional, then test 3.5, 5, and 7 bps.

If 2025 cannot be recovered, use quarterly rolling OOS folds over 2021–2024 and retain the 43 real 2026 days as a small final shadow test. Do not call 43 days decisive.

### Weeks 3–4: pre-register one restrained protocol

Candidates only:

- locked single-tree incumbent;
- a distilled `realized_vol_30m + minute_of_day` rule;
- one regularized multi-tree challenger;
- causal time-of-day-only, volatility-only, random-entry, and unconditional-long baselines.

Pre-register:

- folds and embargo;
- exact feature list;
- causal threshold rule;
- costs;
- trade cap;
- tie handling;
- roll exclusions;
- global experiment count;
- selection metric;
- minimum trades per fold;
- a new untouched final period.

No further feature-family expansion until this protocol finishes.

### Weeks 4–5: execute and attribute the decay

Report deltas sequentially:

1. proxy → actual price stream;
2. legacy percentile → causal threshold;
3. same-close → next-open entry;
4. close-only → OHLC first-touch;
5. old ₹ cost → full date-versioned cost;
6. single tree → multi-tree.

That tells you whether the old edge came from the model, the proxy, look-ahead, fill optimism, or low costs.

Mandatory outputs:

- daily—not merely per-trade—Sharpe and bootstrap CI;
- 20-day moving-block bootstrap, 10,000 draws;
- cumulative DSR experiment count;
- yearly/quarterly stability;
- score-quintile monotonicity;
- cost and entry-jitter stress;
- comparison against all baselines.

### Weeks 5–6: make the decision

- **Promote to paper only if the causal locked incumbent or challenger clears every gate.**
- If neither clears, kill Sleeve F v0 and retain the full autopsy.
- If results are positive but statistically thin, continue real-feed paper measurement with no promotion.
- Do not spend effort on more geometries until the original model question is settled.

## 5. Branch status

### Is the work commit-worthy?

**The work is worth preserving, but the branch is not commit-ready as one blob.**

Commit-worthy material:

- real-futures normalization and QA;
- conservative replay mechanics;
- signal-family implementations and tests;
- day-cluster statistics work;
- negative-rule autopsy;
- crash causes and resulting data-quality policy.

Not commit-worthy as currently represented:

- calling this original-router revalidation;
- claiming grids were committed when the runner does not verify it;
- calling a repeatedly reused period sealed;
- calling the interval 95%;
- implying baselines were beaten or evaluated;
- storing 165 MB of repetitive summaries in git.

The summaries are approximately:

- Phase 2: 84 MB
- Extended: 14 MB
- Phase 3: 67 MB

Do not commit those giant raw JSON files directly. Commit a compact canonical artifact containing:

- config-level metrics;
- annual/quarterly folds;
- selected-candidate details;
- data/config/model hashes;
- global experiment count;
- autopsy verdict;
- pointer or checksum for raw artifacts stored elsewhere.

The two crash logs are useful only as a short provenance note: zero OI and zero-volume rows caused strict-validation failures. Preserve that diagnosis, not necessarily the raw attempt directories.

Recommended commit structure:

1. Futures normalization and feed-QA changes with tests.
2. Replay/statistics infrastructure with corrected CI semantics.
3. Rule families and pre-registered config manifests.
4. Compact negative-results autopsy explicitly titled **“new rule grids; original LightGBM not evaluated.”**

No files were changed, and I did not run tests because the review was explicitly read-only.
