# intraday-pred v1 design specification

**Status:** frozen planning specification  
**Scope:** pure prediction for NIFTY index futures at H15 and H60  
**Implementation owner:** Luna  
**No training or implementation is authorized by this document.**

## 1. Diagnosis of v0

### 1.1 What v0 actually predicted

The output called “magnitude” was not magnitude in the usual sense. It was a direct regression of the **signed** future log return:

\[
r_h(t)=10{,}000\log\left(\frac{F_{t+h}}{F_t}\right)
\]

The direction classifier independently predicted `DOWN/FLAT/UP`, while the Huber regressor attempted to estimate the conditional signed mean or median of the same noisy return.

That architecture asks two independent models to recover the return sign:

- The classifier estimates direction probabilities.
- The regressor must rediscover direction before it can predict a useful signed value.

For a nearly symmetric, median-near-zero intraday return distribution, Huber/MAE-like regression is rewarded for shrinking toward zero. That is exactly what happened.

### 1.2 Evidence from the artifacts

The failure is systematic, not a single bad final split:

- All eight dev horizon/fold regressors had negative R².
- H15 dev daily IC was small and inconsistent: `+0.0356`, `+0.0164`, `−0.0108`, `+0.0071`.
- H60 daily IC was negative in every fold: `−0.0556`, `−0.0918`, `−0.1510`, `−0.1221`.
- On the final untouched H15 test:
  - model MAE `9.0666 bps`
  - zero baseline `9.0732 bps`
  - R² `−0.00033`
  - Pearson `0.0078`
  - daily IC `−0.0095`
- On final H60:
  - model MAE `17.8296 bps`
  - zero baseline `17.8249 bps`
  - R² `−0.00051`
  - Pearson `0.0078`
  - daily IC `−0.1828`

The H15 MAE advantage over zero was only `0.0066 bps`, about `0.07%`. H60 was worse than zero.

The linear magnitude baseline was materially worse, especially on final H60 (`20.48 bps` MAE), so the result is not evidence that a simpler signed-return model was overlooked.

### 1.3 Feature ceiling, target formulation, and horizon

**Primary diagnosis: target formulation.**

The signed-return regression target is poorly aligned with the claimed output and with MAE/Huber optimization. Its optimum is close to zero unless features carry strong conditional sign information. The direction classifier’s modest AUC shows that such information exists, but not strongly enough for an independent signed regressor.

**Secondary diagnosis: feature ceiling for conditional signed return.**

V0 already had 42 features spanning:

- futures returns and realized volatility
- ATR and VWAP behavior
- opening-range state
- OI changes and OI/price quadrants
- volume/OI features
- raw spot–futures basis and its 30-minute change
- regime, time, gap, and bar-shape features

Thus “add basis” alone is not a credible remedy: `basis` and `basis_chg_30m` were already present and almost completely populated in C1. Cross-asset data may raise the ceiling, but it cannot explain away the failed target.

**Horizon is an amplifier, not the root cause.**

H60 is clearly less stable: weaker later dev folds, negative daily IC throughout development, and strongly negative final daily IC. But H15 also had R² approximately zero and no rank skill. Dropping H60 alone would not fix magnitude.

### 1.4 Required attribution experiment

V1 must retain these fixed diagnostic ablations:

- **V1-A, target-only:** v0 features plus the reformed magnitude target.
- **V1-B, spot/basis:** V1-A plus audited spot and reconstructed basis features.
- **V1-C, production:** V1-B plus BANKNIFTY-relative and lagged VIX features.

V1-C is the pre-registered production candidate. A and B diagnose whether any recovery comes from target formulation or new information; they do not create a post-results model-selection search.

---

## 2. V1 scope decision

### Lever order

1. **Reformulate magnitude first.**
   Separate direction from nonnegative move size and align the model loss with the reported metric.

2. **Add audited cross-asset features second.**
   Add NIFTY spot dynamics, reconstructed lagged basis, BANKNIFTY relative behavior, and strictly lagged daily VIX.

3. **Defer the per-stock extension to v2.**

### Per-stock decision

The `nifty500/` directory contains 536 minute files. The inspected `360ONE` and `RELIANCE` samples have the expected `date/open/high/low/close/volume` schema and long histories.

They are nevertheless unsuitable for v1 because the supplied inventory does not establish:

- point-in-time NIFTY 500 membership
- delisted or removed constituents
- corporate-action adjustment policy
- symbol-history mappings
- stable liquidity eligibility
- a cross-sectional missing-data contract

Using the current directory as a historical universe would introduce survivorship and availability bias. Per-stock prediction is therefore **v2-only**, after a point-in-time universe and adjustment contract exists.

### Horizon scope

Keep H15 and H60. Do not add H5, H30, or more targets in v1. The primary purpose is to give magnitude one controlled, interpretable attempt rather than expand the multiple-testing surface.

---

## 3. Data and feature specification

### 3.1 Source audit

| Source | Observed schema/range | Decision |
|---|---|---|
| `indices_minute/BANKNIFTY_minute.csv` | `date,open,high,low,close,volume`; only 6,367 rows from 2026-04-27 to 2026-05-25 | Reject for v1 development |
| `indices_minute/NIFTY_minute.csv` | Same schema and same 2026-only range | Reject |
| `nifty_intraday/NIFTY 50_minute.csv` | 1,062,380 rows; ISO timestamp plus OHLCV; history from 2015 | Use as NIFTY spot |
| `banknifty_intraday/bank-nifty-1m-data.csv` | 1,046,599 rows; `Instrument,Date,Time,Open,High,Low,Close`; `DD-MM-YYYY` dates | Use after normalization |
| `nifty_intraday/INDIA VIX_day.csv` | 4,291 daily rows; mixed date/date-time strings; OHLCV | Use previous completed source day only |
| `nifty500/*.csv` | 536 files; inspected examples have minute OHLCV | Defer |

The long NIFTY and BANKNIFTY minute sources match approximately 99.99% of cutoff-safe C1 timestamps. Audit findings that must be handled explicitly:

- BANKNIFTY contains 3,690 duplicated timestamps; the duplicates inspected were value-identical.
- Both minute sources contain non-standard-session timestamps.
- VIX mixes date-only and date-time text.
- All timestamps are timezone-naive.
- Minute-bar labels and feed availability semantics are not documented.

### 3.2 External-data seam

Create one deep external-data module with a small interface:

```text
load_intraday_external_features(decision_rows, cutoff, mode) -> aligned feature frame + audit
```

Its adapters own schema parsing, normalization, duplication policy, session filtering, lagging, missingness, and source auditing. Callers must not perform ad hoc CSV joins.

### 3.3 Global alignment contract

For a futures decision timestamp \(t\):

- Treat timestamps as naive Asia/Kolkata exchange-local time. Do not timezone-convert.
- Restrict standard-session minute sources to `09:15–15:29`.
- External minute information is usable only through \(t-1\) minute.
- Require an exact external bar at \(t-1\); do not forward-fill an older intraday value.
- Return windows require exact same-session endpoints. Rolling volatility requires a contiguous window.
- Basis uses both futures and spot at \(t-1\), avoiding asynchronous \(F_t/S_{t-1}\).
- Missing auxiliary data produces NaN plus a missing flag; it does not exclude an otherwise valid futures row.
- Never fill across sessions.
- Conflicting duplicate timestamps are fatal. Value-identical duplicates may be collapsed and counted in the audit.
- All data reads and manifests report source hash, raw rows, accepted rows, duplicates, off-session rows, minimum/maximum accepted timestamp, and join coverage.

For C1, the loader must predicate-scan:

```text
datetime < 2025-07-01
```

and assert:

```text
max(datetime) <= 2025-06-30 15:29:00
```

until the separately authorized final unseal.

### 3.4 Exact new features

Notation:

- \(F^-_t=F_{t-1}\)
- \(S^-_t=S_{t-1}\), from long-history NIFTY spot
- \(K^-_t=K_{t-1}\), from BANKNIFTY
- \(L_k(X,t)=\log(X_t/X_{t-k})\)
- All intraday windows are same-session exact-timestamp windows.

#### NIFTY spot

Source: `nifty_intraday/NIFTY 50_minute.csv`

| Feature | Formula |
|---|---|
| `spot_log_ret_1m` | \(L_1(S^-,t)\) |
| `spot_log_ret_5m` | \(L_5(S^-,t)\) |
| `spot_log_ret_15m` | \(L_{15}(S^-,t)\) |
| `spot_log_ret_30m` | \(L_{30}(S^-,t)\) |
| `spot_log_ret_60m` | \(L_{60}(S^-,t)\) |
| `spot_rv_15m` | std of the last 15 contiguous one-minute spot returns, annualized with `sqrt(252*375)` |
| `spot_rv_60m` | Same over 60 minutes |
| `spot_missing` | 1 when the exact \(t-1\) bar is absent, otherwise 0 |

Causal justification: all values end one completed external minute before the decision.

#### Reconstructed spot–futures basis

Sources: cutoff-safe C1 futures and long-history NIFTY spot.

Legacy `basis` and `basis_chg_30m` are removed from V1-B/V1-C to avoid retaining two differently aligned basis definitions.

\[
B_t=10{,}000\left(\frac{F^-_t}{S^-_t}-1\right)
\]

| Feature | Formula |
|---|---|
| `basis_bps_l1` | \(B_t\) |
| `basis_delta_5m` | \(B_t-B_{t-5}\) |
| `basis_delta_15m` | \(B_t-B_{t-15}\) |
| `basis_delta_30m` | \(B_t-B_{t-30}\) |
| `basis_z_60m` | z-score of \(B_t\) against the same-session trailing 60-minute window ending at \(t-1\), minimum 30 observations |
| `basis_missing` | 1 if either synchronized price is absent |

No theoretical carry adjustment is allowed because expiry and funding inputs are not present. This is an observed basis signal, not “fair-value basis.”

#### BANKNIFTY relative behavior

Source: `banknifty_intraday/bank-nifty-1m-data.csv`

| Feature | Formula |
|---|---|
| `bank_log_ret_5m` | \(L_5(K^-,t)\) |
| `bank_log_ret_15m` | \(L_{15}(K^-,t)\) |
| `bank_log_ret_30m` | \(L_{30}(K^-,t)\) |
| `bank_log_ret_60m` | \(L_{60}(K^-,t)\) |
| `bank_rel_ret_5m` | `bank_log_ret_5m - spot_log_ret_5m` |
| `bank_rel_ret_15m` | Same at 15 minutes |
| `bank_rel_ret_30m` | Same at 30 minutes |
| `bank_rel_ret_60m` | Same at 60 minutes |
| `bank_spot_rv_ratio_30m` | \(\log((RV^{bank}_{30}+\epsilon)/(RV^{spot}_{30}+\epsilon))\), \(\epsilon=10^{-12}\) |
| `bank_missing` | 1 when exact \(t-1\) BANKNIFTY data is absent |

These features measure whether the high-beta banking complex is leading or lagging the broad index without using future bars.

#### Lagged VIX

Source: `nifty_intraday/INDIA VIX_day.csv`

For futures trade date \(d\), select the latest VIX source date strictly less than \(d\). Same-day daily OHLC is prohibited because its high, low, and close are not known intraday.

| Feature | Formula |
|---|---|
| `vix_close_l1d` | Previous completed VIX close |
| `vix_log_ret_1d` | Log change between the previous two completed VIX closes |
| `vix_log_ret_5d` | Five-observation log change ending on the previous completed VIX day |
| `vix_z20` | Previous close z-score against the preceding 20 completed observations |
| `vix_range_l1d` | `(previous high - previous low) / previous close` |
| `vix_stale_calendar_days` | \(d-\text{selected VIX date}\) |
| `vix_missing` | 1 if no acceptable prior value exists |

A VIX value older than five calendar days is treated as missing. Weekend and holiday reuse within that limit remains observable through `vix_stale_calendar_days`.

---

## 4. Target and model changes

### 4.1 Direction target

Keep the v0 direction target unchanged for direct comparability:

\[
\sigma_{h,t}=\frac{RV_{30,t}}{\sqrt{252\cdot375}}\sqrt{h}
\]

\[
b_{h,t}=\max(0.0002,\ 0.25\sigma_{h,t})
\]

- `UP` when \(r_h>b_{h,t}\)
- `DOWN` when \(r_h<-b_{h,t}\)
- `FLAT` otherwise

Same-session exact future timestamps remain mandatory.

### 4.2 Magnitude target

Replace signed return regression with **nonnegative conditional move size**.

Let signed return in basis points be:

\[
r^{bps}_{h,t}=10{,}000\log(F_{t+h}/F_t)
\]

Define the causal scale:

\[
s_{h,t}=\max\left(2,\ 10{,}000\sigma_{h,t}\right)
\]

and normalized magnitude:

\[
u_{h,t}=\frac{|r^{bps}_{h,t}|}{s_{h,t}}
\]

The model predicts the conditional median of \(u_h\). The reported magnitude is:

\[
\widehat{M}_{h,t}
  =s_{h,t}\max(0,\widehat{Q}_{0.5}(u_h\mid X_t))
\]

Output name:

```text
median_abs_move_bps
```

Remove `expected_log_return_bps`. V1 must not call a median absolute move an expected signed return.

Direction probabilities and magnitude remain separate outputs. No synthetic signed expectation is part of the primary contract.

### 4.3 Models

**Direction**

Retain the v0 LightGBM classifier and frozen structural parameters:

- multiclass objective
- 350 estimators
- learning rate `0.03`
- 31 leaves
- `min_child_samples=400`
- existing regularization and bagging
- class weighting
- seed `20260718`
- deterministic mode

No direction hyperparameter search is permitted in v1.

**Magnitude**

Use one LightGBM quantile regressor per horizon:

- objective `quantile`
- alpha `0.50`
- otherwise the same tree capacity, regularization, deterministic seed, and thread settings as direction

Quantile loss directly matches the primary MAE metric and avoids tail domination.

### 4.4 Calibration

- Retain session-weighted top-label correctness calibration.
- Use isotonic regression when the existing sufficiency checks pass; retain the frozen Platt fallback.
- Fit a separate confidence calibrator per horizon.
- Do not calibrate magnitude with final-test outcomes.
- Report magnitude calibration by predicted-magnitude decile: predicted median, realized median, count, and monotonicity.
- Calibration rows must not enter either base model’s training set.

---

## 5. Evaluation plan

### 5.1 Development folds

Retain v0 folds 1–4 and add one pre-2025 fold:

| Fold | Train | Calibration | OOS |
|---|---|---|---|
| 1 | 2021-01-01 → 2022-06-23 | 2022-07-01 → 2022-09-23 | 2022-10-01 → 2022-12-31 |
| 2 | 2021-01-01 → 2022-12-24 | 2023-01-01 → 2023-03-24 | 2023-04-01 → 2023-06-30 |
| 3 | 2021-01-01 → 2023-06-23 | 2023-07-01 → 2023-09-23 | 2023-10-01 → 2023-12-31 |
| 4 | 2021-01-01 → 2023-12-24 | 2024-01-01 → 2024-03-24 | 2024-04-01 → 2024-06-30 |
| 5 | 2021-01-01 → 2024-06-23 | 2024-07-01 → 2024-09-23 | 2024-10-01 → 2024-12-31 |

Each transition keeps a seven-calendar-day embargo, and label-end purging remains horizon-specific.

2025H1 is excluded from development evaluation because its outcomes have already informed this design.

### 5.2 Baselines

**Direction**

- random balanced accuracy: `1/3`
- always-majority class
- last-five-minute sign
- multinomial linear model
- frozen v0 LightGBM architecture retrained on identical rows

**Magnitude**

All baseline parameters are learned only from each fold’s training block:

1. zero magnitude, sanity baseline
2. unconditional training median of `abs(return_bps)`
3. scale-only baseline:
   \[
   \operatorname{median}_{train}(u_h)s_{h,t}
   \]
4. 30-minute time-bucket scale baseline:
   \[
   \operatorname{median}_{train}(u_h\mid time\ bucket)s_{h,t}
   \]
   with global-median fallback

“Best baseline” means the lowest OOS MAE among this fixed set. No baseline is selected using calibration or final outcomes.

### 5.3 Metrics

Report for every fold, horizon, and candidate:

**Direction**

- accuracy
- balanced accuracy
- macro F1
- macro one-vs-rest AUC
- UP-vs-DOWN AUC
- per-class precision and recall
- multiclass Brier score
- confusion matrix

**Confidence**

- Brier score
- ECE
- reliability curve
- accuracy by confidence decile
- Spearman correlation between decile number and empirical accuracy
- top-decile minus bottom-decile accuracy

**Magnitude**

- MAE and RMSE in absolute-move bps
- relative MAE skill:
  \[
  1-\frac{MAE_{model}}{MAE_{best\ baseline}}
  \]
- R², diagnostic only
- Pearson correlation
- daily Spearman IC
- positive-day IC fraction
- predicted-vs-realized magnitude deciles
- quarter, time-of-day, regime, and missing-source slices

Metrics are calculated on:

- all valid minute decisions, for v0 comparability
- non-overlapping decisions anchored at 09:30 and every H minutes thereafter

Daily IC uses the full minute sample because H60 produces too few non-overlapping observations per day for a stable within-day rank. Uncertainty uses a 2,000-draw paired session-block bootstrap.

### 5.4 Pre-registered development gates

A horizon passes independently.

| Output | Development pass standard |
|---|---|
| Direction | Pooled OOF balanced accuracy ≥ `0.370`; macro AUC ≥ `0.540`; BA above `1/3` in at least 4/5 folds |
| Direction strengthening | V1-C improves pooled BA over the frozen v0 comparator by ≥ `0.003` at one horizon and degrades the other by no more than `0.002` |
| Confidence H15 | ECE ≤ `0.030`; decile-accuracy Spearman ≥ `0.80`; top-minus-bottom decile ≥ `0.08` |
| Confidence H60 | ECE ≤ `0.050`; decile-accuracy Spearman ≥ `0.80`; top-minus-bottom decile ≥ `0.08` |
| Magnitude | Relative MAE skill ≥ `2%`; positive skill in at least 4/5 folds; daily IC mean ≥ `0.05`; positive-day fraction ≥ `0.55`; non-overlapping MAE skill ≥ `1%` |

Failure of one output cannot be offset by another output’s success.

### 5.5 Honest final-test protocol

The 2025H1 test is burned. It cannot be called untouched again and must not determine v1 promotion.

After the v1 code, feature manifest, thresholds, and hashes are frozen:

- **Train:** 2021-01-01 through 2025-03-24
- **Embargo:** 2025-03-25 through 2025-03-31
- **Calibration:** 2025-04-01 through 2025-06-23
- **Embargo:** 2025-06-24 through 2025-06-30
- **New one-shot sealed test:** 2025-07-01 through 2025-12-31

Using 2025H1 for final training/calibration is allowed only because it is explicitly reclassified as known historical data. It must not receive a v1 test score.

The H2 test is opened once, after:

- registration and source hashes are frozen
- all development gates have passed
- the exact V1-C candidate is fixed
- no final-test values have been inspected
- the access/unseal is recorded in the manifest

The frozen v0 comparator and V1-C may be evaluated together as one pre-registered final protocol.

### 5.6 Final pass standards

| Output | Sealed H2 success standard |
|---|---|
| Direction | BA ≥ `0.370`, macro AUC ≥ `0.540`, and BA improvement over frozen v0 comparator ≥ `0.003` |
| Confidence H15 | ECE ≤ `0.030`, decile Spearman ≥ `0.80`, top-minus-bottom decile ≥ `0.08` |
| Confidence H60 | ECE ≤ `0.050`, decile Spearman ≥ `0.80`, top-minus-bottom decile ≥ `0.08` |
| Magnitude | MAE skill ≥ `2%`; paired session-bootstrap 95% lower bound for skill > `0`; daily IC mean ≥ `0.05`; positive-day fraction ≥ `0.55`; non-overlapping MAE skill > `0` |

---

## 6. Kill criteria

### Signed-return regression

The v0 signed Huber magnitude formulation is already killed. It must not be revived through hyperparameter tuning in v1.

### Reformed magnitude

Apply kill decisions per horizon:

1. If V1-C fails the development magnitude gate at a horizon, that horizon is not taken to the sealed test.
2. If neither horizon passes development, do not open the sealed test for magnitude. Retire learned magnitude for this feature class.
3. If a horizon reaches the sealed test but fails any of:
   - positive paired-bootstrap MAE skill
   - `2%` minimum skill
   - `0.05` daily IC
   then suppress its magnitude output.
4. If both horizons are suppressed, future index-model versions output direction and confidence only.

There is no post-final rescue sweep. Changing leaves, loss parameters, seeds, feature subsets, or flat bands after seeing H2 is prohibited.

Magnitude may be reopened only with a structurally new information class, such as options-surface state, order-book data, or verified event/news timing. More transformations of the same futures, spot, BANKNIFTY, and lagged daily VIX class do not qualify.

---

## 7. Implementation plan for Luna

V0 modules and artifacts remain frozen. V1 should be implemented in parallel modules so its interface and evidence cannot silently alter v0 reproduction.

### Ticket 1: Freeze registration

**Files**

- `registrations/intraday-pred-v1/registration.md`
- `registrations/intraday-pred-v1/feature_manifest.json`
- `registrations/intraday-pred-v1/evaluation_protocol.json`

**Acceptance criteria**

- Encodes the formulas, source paths, candidate ladder, fold dates, thresholds, seed, and final unseal protocol from this specification.
- Contains hashes for every frozen registration file.
- Explicitly marks 2025H1 burned and 2025H2 sealed.
- No model execution is possible until these artifacts exist.

**New tests:** 0

### Ticket 2: External-data adapter

**Files**

- `features/intraday_external.py`
- `tests/test_intraday_external.py`

**Acceptance criteria**

- Parses ISO NIFTY timestamps, DMY BANKNIFTY date/time, and mixed VIX dates.
- Filters standard sessions and cutoff before alignment.
- Collapses identical BANKNIFTY duplicates and rejects conflicts.
- Applies exact \(t-1\) minute alignment and previous-source-day VIX alignment.
- Returns features plus a source audit.
- Never forward-fills intraday values or crosses sessions.

**New tests:** 4

### Ticket 3: V1 target contract

**Files**

- `contracts/intraday_targets_v1.py`
- `tests/test_intraday_targets_v1.py`

**Acceptance criteria**

- Reuses the frozen direction definition exactly.
- Produces signed return bps, causal scale, normalized absolute magnitude, and validity columns.
- Rejects missing or cross-session future timestamps.
- Demonstrates direction-label parity with v0 on identical synthetic input.

**New tests:** 3

### Ticket 4: V1 feature module

**Files**

- `features/intraday_prediction_v1.py`
- `tests/test_intraday_prediction_v1_features.py`

**Acceptance criteria**

- Implements V1-A, V1-B, and V1-C manifests exactly.
- Removes legacy basis columns from B/C.
- Computes every formula in this document.
- Emits family missing flags.
- A mutation to any source value at \(t\) or later cannot change external features for decision \(t\).
- Forbidden target or execution columns cannot enter the matrix.

**New tests:** 3

### Ticket 5: V1 models

**Files**

- `models/intraday_predictor_v1.py`
- `tests/test_intraday_predictor_v1.py`

**Acceptance criteria**

- Direction parameters match the frozen v0 architecture.
- Magnitude uses quantile alpha `0.50` on normalized absolute magnitude.
- Predictions are nonnegative and named `median_abs_move_bps`.
- No `expected_log_return_bps` output exists.
- Repeated seeded fits on a small fixture produce identical predictions.

**New tests:** 1

### Ticket 6: Folds, baselines, and metrics

**Files**

- `evidence/intraday_folds_v1.py`
- `evidence/intraday_metrics_v1.py`
- `tests/test_intraday_evidence_v1.py`

**Acceptance criteria**

- Implements the five development folds and final split exactly.
- Purges label ends at every transition.
- Enforces the development firewall.
- Implements all fixed magnitude baselines and relative skill.
- Produces full and non-overlapping metrics plus paired session-bootstrap confidence intervals.
- Encodes pass/fail mechanically from registration thresholds.

**New tests:** 2

### Ticket 7: Deterministic runner and artifacts

**Files**

- `scripts/run_intraday_prediction_v1.py`
- `tests/test_run_intraday_prediction_v1.py`

**Acceptance criteria**

- C1 is predicate-scanned before materialization and cutoff-asserted.
- Development mode cannot access 2025-07+ rows from any source.
- Final mode requires an explicit frozen-registration hash and unseal flag.
- Runs all three diagnostic candidates but treats only V1-C as production.
- Writes:
  - `manifest.json`
  - `fold_metrics.json`
  - `candidate_ablation.json`
  - `report.md`
  - horizon prediction Parquets
- Manifest includes source hashes, audit counts, feature manifest, model parameters, package versions, seed, fold dates, and registration hash.
- Two identical synthetic smoke runs produce byte-equivalent metrics and manifest content apart from explicitly excluded runtime metadata.

**New tests:** 2

**Total new tests: 15.**

No prototype, hyperparameter sweep, training run, or sealed-data access belongs inside these tickets until the registration artifacts are reviewed and frozen.

