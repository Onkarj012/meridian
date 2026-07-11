# Sleeve F — Joint Forward Plan (Fable × Sol mediated)

**Date:** 2026-07-10
**Status:** Approved direction, not yet started
**Horizon:** 2–6 weeks
**Source:** Mediated review — Fable 5 + Sol (gpt-5.6-sol, high effort), branch exploration by opencode GO (kimi-k2.7-code). Full Sol review: `brain/plans/sleeve-f-sol-review-full.md` (407 lines).

---

## Context: why this plan exists

Phase 2 never tested the thing it was supposed to test. All 88 configs across the three grids (phase2: 36, extended: 24, phase3: 28) are hand-written rule signals — breakout, momentum, VWAP, opening-range, basis. The validated LightGBM router (`final_long.lgb`, 39 features, 40/30/60 geometry, top-15%-of-day selection, ≤3 trades/day) was never ported; `models/lightgbm.py` is an 8-line stub. Fable and Sol found this independently.

Consequences:

1. **Rule grids are dead** — correct kill, gross EV ≈ 0, 3.5 bps cost eats everything. Best cell +2.35 bps EV with CI-low −1.73; dies at 5 bps stress.
2. **The original router is untested** on real data.
3. **The original Sharpe 2.17 is an unresolved claim** — its forward walk gates morning entries with each day's *full-day* 85th score percentile (`groupby("trade_date").transform(quantile)`), verified at `intranet_optinet/scripts/research/forward_walk.py:471`. Look-ahead in trade selection.

### The "15% hit rate" — corrected

15% was the worst sparse per-day fold cell, not a grid statistic. Real net-win rates: phase2 33–43%, extended 37–48%, phase3 45–49%. The real problem is EV, not hit rate. Also note `hit_rate` in summaries = net-profitable trades including timeouts; `touch_rate` is the true label statistic. The conflation hides label economics.

### Label economics (P1 principle, retroactively applied)

Tight geometries were doomed before training. 12/8/10 needs 57.5% binary breakeven vs observed touch of 23–30%. Even 40/25/60 needs 43.9% vs ~24–30% observed. Every small-target grid failed P1 before a single backtest ran. Additionally, P1's binary breakeven formula is itself insufficient: the original router made most of its money on **time exits** (875 of 1,128 trades, ~₹313k). Viability check must be:

```
EV = pT·T − pS·S + pTO·E[R_TO] − C
```

### Methodology defects to fix (Sol + opencode, converging)

- CI mislabeled: 5th percentile of 1,000 day-cluster draws = **90%** CI, not 95%. Doc requires 20-day blocks × 10k draws.
- DSR uses per-grid k (36/24/28), not cumulative k ≥ 88; correlated configs understate the correction further.
- `beats_baselines` never evaluated — runner passes no promotion flags, so `false` means "no evidence", not "lost".
- Holdout "seal" spent three times — phase2, extended, phase3 all touched the same holdout. Not sealed anymore.
- Runner writes `committed_grid: true` without checking git; both new grid configs are untracked while claiming committed.
- Data gap: **2025 has zero days** (2020–24 + 43 days of 2026). Original Nov-2024→May-2026 OOS window cannot be reproduced on real data as-is.
- Not walk-forward: 80/20 chronological split, no retraining, no rolling folds.
- Negative results remain credible: replay is conservative (next-bar-open, OHLC first-touch, stop-wins ambiguity), data is real (only 48 zero-OI / 666 zero-vol rows of 466k).

---

## Week 1 — Autopsy, stop digging

- [ ] Kill rule grids formally.
- [ ] Reclassify all three runs as **"rule-family autopsy — original LightGBM not evaluated"**.
- [ ] Mark holdout as spent (three touches).
- [ ] Record global trial count k ≥ 88 for all future DSR corrections.
- [ ] Strip false `committed_grid` / `sealed` / "95% CI" claims from summaries and docs.

## Weeks 1–2 — Port the incumbent exactly

- [ ] Port `final_long.lgb` + all 39 features + 40/30/60 geometry + long-only + percentile selection policy + 3/day cap + sizing + halt logic.
- [ ] **Bridge test:** reproduce the original 1,128-trade ₹429k ledger within a *pre-declared* tolerance. If it fails, stop — no experimental bridge, no proceeding with an unverified port.
- [ ] Build two variants:
  - **(a) Legacy parity** — same-day full-day percentile threshold. Explains the old number. Unpromotable by construction (contains the look-ahead).
  - **(b) Causal candidate** — threshold from trailing/prior-day scores only, next-bar-open fills, OHLC first-touch.
- [ ] The delta between (a) and (b) = how much of Sharpe 2.17 was look-ahead. This is the single most important number the plan produces.

## Weeks 2–3 — Data contract

- [ ] Obtain real 2025 front-month futures data, or state plainly that the original OOS window is unrecoverable.
  - Fallback: rolling quarterly folds over 2021–24; keep 2026's 43 days as shadow only.
- [ ] Real contract tokens/expiries, roll rules, date-versioned costs.
- [ ] Convert the old flat ₹105 cost to bps; stress at 3.5 / 5 / 7 bps.

## Weeks 3–4 — Pre-register one restrained protocol

- [ ] Candidates: locked incumbent (causal variant), distilled `realized_vol_30m + minute_of_day` rule, one multi-tree challenger, causal baselines.
- [ ] Freeze in git before running: folds, features, thresholds, costs, trial count k, selection metric, and a new untouched final evaluation period.
- [ ] No new signal families until this protocol completes.

## Weeks 4–5 — Execute, attribute decay stepwise

Change one thing at a time; measure the Sharpe/EV delta at each step:

1. proxy data → real data
2. legacy threshold → causal threshold
3. close fills → next-bar-open fills
4. close-only touch → OHLC first-touch
5. old costs → full date-versioned costs
6. single tree → multi-tree

Result shows whether the old edge was model, proxy, look-ahead, fills, or cheap costs.

## Weeks 5–6 — Decide

- Causal incumbent or challenger clears gates → **paper trade**.
- Neither clears → **kill Sleeve F v0**, keep the autopsy, shift effort to Sleeve M/X.
- Thin-positive → keep measuring, **no promotion**.

---

## Plan-doc fixes to fold in (Sol, condensed)

Keep: promotion culture, full costs, PIT discipline, autopsies, CI-low-is-the-number.

Fix:

1. Sleeve F labeled "promoted" (§1) while the Phase 2 gate says revalidation required — reclassify as quarantined candidate.
2. Concentration gate needs an applicability matrix; for futures replace with P&L concentration by day / expiry-week / time-bucket / regime.
3. One-gauntlet-fits-all breaks for single-index futures (stock embargo/splits meaningless); overlapping 120–180 min labels need their own embargo length.
4. P1 formula: use the full-EV form above, not binary breakeven.
5. `worst_fold ≥ 0` implies `70% positive folds` — drop the redundant gate or make them independent.
6. Sealed-test governance must be enforced mechanically: runner should verify git-tracked status before writing `committed_grid: true`.
7. Add portfolio-level risk (sleeves share NIFTY beta); specify futures mechanics (rolls, expiry, lot-size history, date-versioned taxes).
8. Calibration: add Brier score, calibration slope, and value-by-bucket alongside ECE.
