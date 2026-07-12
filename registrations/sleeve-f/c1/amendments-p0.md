# Sleeve F — Campaign 1 pre-freeze registration amendments (P0-2 … P0-4)

**Status:** pre-freeze defect corrections, ratified (zero k — no outcome data consulted).
**Amends:** Sleeve F Model Training & Selection Protocol v1 (local planning doc; the frozen registration in this directory is the binding copy).
**k ledger:** Campaign 1 = k = 95 (locked).

These three amendments correct execution-semantics defects identified during adversarial review. None was informed by any outcome run. They bind every Campaign 1 candidate, baseline, and replay.

---

## P0-2 — Late-entry honesty (timeout horizon)

The execution-consistent label and the replay both use:

> **timeout = min(60 bars, last same-session bar)**, exit at that bar's close.

- No label or trade horizon ever crosses a session boundary. A decision at 14:30 or later has a truncated horizon ending at the 15:29 bar close.
- A decision bar with no next same-session bar (the final bar) is ineligible — there is no next-bar open to enter at.
- **Slice reporting obligation:** every evaluation report (walk-forward, holdout, shadow) reports the ≤ 14:29 and ≥ 14:30 decision-time slices separately, in addition to the pooled result. Truncated-horizon trades may not be silently pooled.
- Replay boundary tests are pinned at decision bars 14:29 and 14:54 (see `tests/test_sleeve_f_labels.py`).

## P0-3 — Point-in-time boundary

- All features for a decision at bar *t* are computed from information up to and including **bar *t* close**. Nothing from bar *t+1* onward — price, volume, OI, spot — may enter the feature vector.
- Entry, when taken, is at **bar *t+1* open**. Barriers (+0.40% / −0.30%) are measured from the realized entry price, not the decision close.
- Regime thresholds are causal: quantiles from the preceding 60 completed sessions, matched by minute-of-day, ≥ 20 valid prior sessions required, else the row is ineligible (`features/causal_regime.py`, config hash pinned in the freeze document). The incumbent full-frame regime builder is noncausal and is confined to the attribution/parity track; it may not touch any deployable candidate.

## P0-4 — NaN and missing-data policy (frozen enumeration)

No feature is ever imputed. **No cross-session forward-fill anywhere.** The 39 inputs split into exactly two classes:

### Class A — NaN ⇒ row ineligible (excluded from training and from live decisions)

Core price/time features, which must exist for every legitimate post-warm-up bar:

`ret_1m ret_5m ret_15m ret_30m ret_60m · log_ret_1m log_ret_5m log_ret_15m log_ret_30m · atr_5m atr_15m atr_30m · realized_vol_5m realized_vol_15m realized_vol_30m · vwap_dev vwap_slope_5m · or_dist_high or_dist_low or_breakout_up or_breakout_dn · minute_of_day hour_of_day session_progress day_of_week · gap_pct consec_bars ema_slope`

- Warm-up rows (first 60 minutes of the session, i.e. decisions before 09:45 given `ret_60m`) are ineligible by construction — eligibility begins 09:45, unchanged from the incumbent.
- A Class-A NaN appearing after warm-up indicates a data defect; the row is ineligible and the occurrence is logged (it feeds the session-quality ledger, never a silent drop).

### Class B — NaN passes through to LightGBM as NaN

Features whose NaN encodes a real, recurring market condition (zero denominators, missing auxiliary series). LightGBM's native NaN routing handles them; they never gate row eligibility:

| Feature(s) | NaN cause |
|---|---|
| `oi_chg_1m oi_chg_5m oi_chg_30m` | prior OI = 0 (zero denominator) |
| `vol_oi_ratio` | OI = 0 |
| `vol_zscore` | zero rolling volume std |
| `basis basis_chg_30m` | spot unavailable (registered spot-gap days 2026-06-01, 2026-06-04) — never imputed across the gap |

OI-quadrant flags (`oi_long_buildup oi_short_buildup oi_short_cover oi_long_unwind`) are always 0/1 by construction and carry no NaN state in Campaign 1 (their zero-change mislabeling is a registered C2 repair, not a C1 change).

### Excluded days (restated from §1c of the protocol)

2025-09-26 (truncated) and 2026-06-03 (zero volume, bad close) are dropped entirely. Muhurat sessions absent from the archive stay absent. The 2020–2023 odd-bar-count sessions take their dispositions from the session-quality contract (`runs/sleeve-f-session-quality/`), fixed before the freeze commit.

---

*Freeze artifacts (config hashes, exclusion table, fold grid, gates) are assembled in `registration.md` in this directory at the P0-5 freeze commit.*
