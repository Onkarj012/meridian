# Sleeve F — Campaign 1 registration (Phase 1 freeze), k = 95

**Protocol:** Model Training & Selection Protocol v1 (with pre-freeze amendments P0-1…P0-4 in `amendments-p0.md`).
**Frozen at:** the git commit that introduces this file (the freeze commit hash is, by construction, the commit this file first appears in; the runner verifies git-tracked, uncommitted-clean status before any outcome run).
**Discipline:** every inspected variant increments k · one information family per campaign · forward shadow promotes, historical WF develops · no post-hoc pruning · policy return is the only evidence series.

---

## 1. Data and labels

- **Span:** 2020-01-01 → 2025-06-30 walk-forward; holdout 2025-07-01 → 2026-06-30 (one-shot, §7).
- **Archive:** NIFTY front-month 1-minute futures (continuous 2020-01→2026-07-10, Groww-backfilled, validation 403/415 pass — data-contract commits through 98bce8d).
- **Label generators** (`contracts/sleeve_f_labels.py`, tests `tests/test_sleeve_f_labels.py`):
  - Legacy-parity (close-touch, entry=close, ×1.0040/×0.9970, 60-min): **archive parity PASS** — 449,942/449,942 rows, zero mismatches, max |Δfut_close| = 0.0 vs `intranet_optinet/models/router_v0/futures/futures_barrier_labels.parquet` (`runs/sleeve-f-label-parity/`). Blocker gate cleared.
  - Execution-consistent (deployable candidates): decision at bar-t close → entry at t+1 open → barriers +0.40 % / −0.30 % from realized entry → OHLC first-touch, stop wins double-touch → timeout min(60 bars, last same-session bar) at close. Binary label = 1 iff net PnL > 0 after era-dated costs (ties = 0); candidate C target = net return in R.
- **Costs:** era-dated engine `policy/era_costs.py`, `era_table_hash = 5e1954ce973d8a1806dcd11e51a33220bc05eab614bc0cbb4a40f65de658e74f`. Headline = base schedule; stress 3.5 / 5 / 7 bps flat RT (5 bps = kill level). **Registered assumption:** pre-2020-07 stamp duty is proxied at the national 0.002% buy-side rate (state-dependent historically); the 2020-01→06 entry uses the 0.01% sell-side STT in force throughout 2020 and otherwise the earliest sourced fee values.
- **Exclusions:** protocol §1c table + `session-quality-dispositions.md` (2021-02-24 dropped; Muhurat sessions excluded; 376-bar sessions trimmed to 09:15–15:29; label-window contiguity rule).
- **NaN policy:** frozen two-class enumeration in `amendments-p0.md` §P0-4. No imputation, no cross-session forward-fill.

## 2. Features and regime

- **Feature set:** the incumbent 39 (`features/sleeve_f_router.py::FUTURES_FEATURES` + gap/consec/ema/vol columns), computed on real futures volume/OI and real spot close. Warm-up rows (pre-09:45) ineligible.
- **Regime:** causal trailing-60-session builder `features/causal_regime.py`, minute-of-day matched, ≥20 valid prior sessions else ineligible. `regime_config_hash = 75151f226ced8002cf6c76930fcdd273542c88315a8180a913fc74a45c06507a`. Incumbent full-frame `add_regime` is noncausal → attribution/parity track only.
- **Eligibility (unchanged):** ≥09:45, <14:55, exclude 11:00–12:00, exclude compression regime (causal builder's label).

## 3. Candidates (7 selectable configs; hyperparameters frozen, no tuning)

- **A — Retrained router:** LightGBM binary, incumbent architecture verbatim (500 rounds max, lr 0.05, 63 leaves, min_data_in_leaf 200, feature/bagging_fraction 0.85, bagging_freq 5, L1 0.1, L2 1.0, is_unbalance), early stop 30 on purged inner validation; real features + execution-consistent labels, per fold.
- **B — Fixed a-priori rule:** trade when `realized_vol_30m` in top tercile of trailing 60-session causal distribution AND `minute_of_day` in pre-registered windows; parameters from 2020 data only (strictly pre-2021Q1), frozen here.
- **C — Huber challenger:** LightGBM Huber regression on net R (200 trees, depth 2, 4 leaves, lr 0.03, min leaf 100, fractions 0.7, L1 1, L2 10); trades when predicted net R > 0 under the same threshold machinery.
- **D — 4 activity-matched causal baselines:** time-of-day-only, volatility-only, unconditional-long, random-entry (1,000 replicates; seeds derived from (protocol_version, candidate, fold, replicate); compared to median with full null retained).

## 4. Validation

- **Fold grid:** 18 expanding quarterly folds — train 2020-01→2020-12 / test 2021Q1, … , train →2025Q1 / test 2025Q2. Inner validation = last chronological 20 % of training rows, purged boundary.
- **Threshold:** absolute score threshold per fold fitted on inner validation to 0.70 executed trades/full session (target = round_half_up(0.70 × sessions), minimize count error, ties → fewer trades). No percentile-of-day gating.
- **Replay state machine:** protocol §4c verbatim (no entry while open; simultaneity by score then timestamp; entry bar barrier-eligible after entry; halt on realized PnL from next bar; timeout at bar close; expiry/roll explicit; 1.5× only if integer-lot realizable else 1.0×).
- **Purging:** any row whose 60-minute label window overlaps a train/validation/test boundary, in timestamps, applied to folds, inner splits, and threshold fitting.
- **Metrics:** stitched strictly-OOS daily sleeve returns (bps on fixed sleeve capital, zero-trade days included) → Sharpe ×√252; 20-day moving-block bootstrap ×10,000, 95 % CI; score-quintile monotonicity; stability slices (vendor seam 2024-10/11, lot eras, tax eras, expiry weeks, lagged-VIX terciles diagnostic, time buckets); quarterly concentration. Late-entry slices ≤14:29 / ≥14:30 reported separately (P0-2).

## 5. Risk and sizing

R = 30 bps × entry notional; per-trade floor −1R; daily halt −5R (restart-proof); date-specific integer lots; reporting in normalized sleeve-return bps.
**Sleeve capital: ₹1,000,000 (10 lakh INR)** (fixed constant; makes normalized bps concrete). *(User decision #4, frozen by the principal 2026-07-15.)*
*(Principal decision 2026-07-15: candidate B's pre-registered minute windows were never assigned literal values in any frozen text; B is skipped in the C1 walk-forward — the runner records the skip in the report — and is not evaluated in Campaign 1.)*

## 6. Gates (pre-registered, hard)

Viability 6a: net mean daily PnL > 0 at era costs · WF Sharpe ≥ 0.75 · holdout Sharpe ≥ 0.50 · trades ≥300 WF / ≥150 holdout / ≥40 per holdout half / ≥30 post-2026-04 · positive PnL and Sharpe at 5 bps · beats matched baselines (paired) · no quarter/era/bucket domination · operationally executable. Trade minimums are hard fails.
Selection 6b: ΔSharpe ≥ 0.25 in WF and holdout AND paired MBB 95 % CI-low > 0 on mean daily PnL diff. No survivor → kill (6c). Multiplicity: k = 88 + 7 = **95** (attribution excluded only while spec-inert; any attribution-driven spec change → k = 98, protocol v2).
Outcomes 6e: winner → paper-trade the exact frozen artifact; thin-positive → frozen measurement, no promotion; nothing → Sleeve F v0 dead.

## 7. Holdout

2025-07-01 → 2026-06-30, opened exactly once after WF selection frozen. Post-STT (2026-04+) segment reported separately. Once opened, spent.

## 8. Attribution ladder (non-promotable side track)

Frozen `final_long.lgb` under (i) proxy+legacy execution (2.1676), (ii) proxy+causal (2.0667, done), (iii) real inputs+causal. Runs after freeze; cannot modify specs.

## 9. Artifact index (freeze inventory)

| Artifact | Path | Pin |
|---|---|---|
| Causal regime builder | `features/causal_regime.py` | config hash `75151f22…507a` |
| Label generators | `contracts/sleeve_f_labels.py` | parity PASS (`runs/sleeve-f-label-parity/`) |
| Era cost engine | `policy/era_costs.py` | table hash `5e1954ce…e74f` |
| Repaired features (C2 prep, not C1-selectable) | `features/repaired.py` | config hash `5936ef8d…95bf` |
| Session dispositions | `registrations/sleeve-f/c1/session-quality-dispositions.md` | reviewed verdict |
| NaN policy + PIT boundary | `registrations/sleeve-f/c1/amendments-p0.md` | frozen text |
| Activity band procedure | `policy/activity_band.py` | config hash `e9df6ea7…48e5` (band derived at C2; procedure registered now) |
| Decision envelope / execution records | `contracts/decision_envelope.py`, `contracts/execution_records.py` | schema hashes in module |
| VIX clean series | `runs/sleeve-f-calendar-vix/india_vix_clean.csv` | 4,288 rows, 3 dupes resolved last-wins |

No new signal families, features, geometries, or gates after this commit. Any change = protocol v2, new registration, k increments.
