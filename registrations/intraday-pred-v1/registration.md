# intraday-pred v1 frozen registration

Status: frozen planning registration, transcribed from `registrations/intraday-pred-v1/DESIGN_SPEC_SOL.md`.

Scope: pure prediction for NIFTY index futures at H15 and H60. V1 keeps H15 and H60 only; it does not add H5, H30, or more targets. Per-stock prediction is v2-only.

## v0 diagnosis and permanent decision

V0's output called “magnitude” was a direct regression of the signed future log return, while the direction classifier independently predicted `DOWN/FLAT/UP`; the Huber regressor therefore had to rediscover direction before estimating a useful signed value. For a nearly symmetric, median-near-zero intraday return distribution, Huber/MAE-like regression is rewarded for shrinking toward zero, which is what happened. The primary diagnosis is target formulation, with a secondary feature ceiling for conditional signed return; H60 is an amplifier of the instability, not the root cause.

The v0 signed-return Huber magnitude formulation is permanently killed. It must not be revived through hyperparameter tuning in v1. V1 does not produce `expected_log_return_bps`.

The failure evidence was systematic: all eight dev horizon/fold regressors had negative R²; H15 dev daily IC was `+0.0356`, `+0.0164`, `−0.0108`, `+0.0071`; H60 daily IC was negative in every fold (`−0.0556`, `−0.0918`, `−0.1510`, `−0.1221`). On the final untouched H15 test, model MAE was `9.0666 bps` versus zero baseline `9.0732 bps`, R² `−0.00033`, Pearson `0.0078`, daily IC `−0.0095`; on H60, model MAE was `17.8296 bps` versus zero baseline `17.8249 bps`, R² `−0.00051`, Pearson `0.0078`, daily IC `−0.1828`. The H15 advantage was only `0.0066 bps`, about `0.07%`; H60 was worse than zero. The linear magnitude baseline was materially worse, especially on final H60 (`20.48 bps` MAE).

## Direction target

The v0 direction target is unchanged for direct comparability. For horizon (h):

\[
\sigma_{h,t}=\frac{RV_{30,t}}{\sqrt{252\cdot375}}\sqrt{h}
\]

\[
b_{h,t}=\max(0.0002,\ 0.25\sigma_{h,t})
\]

- `UP` when (r_h>b_{h,t})
- `DOWN` when (r_h<-b_{h,t})
- `FLAT` otherwise

Same-session exact future timestamps remain mandatory.

## Magnitude target reform

The signed return in basis points is:

\[
r^{bps}_{h,t}=10{,}000\log(F_{t+h}/F_t)
\]

The causal scale is:

\[
s_{h,t}=\max\left(2,\ 10{,}000\sigma_{h,t}\right)
\]

The normalized nonnegative magnitude is:

\[
u_{h,t}=\frac{|r^{bps}_{h,t}|}{s_{h,t}}
\]

The model predicts the conditional median of (u_h). The reported magnitude is:

\[
\widehat{M}_{h,t}
  =s_{h,t}\max(0,\widehat{Q}_{0.5}(u_h\mid X_t))
\]

The output name is `median_abs_move_bps`. Direction probabilities and magnitude remain separate outputs. No synthetic signed expectation is part of the primary contract.

## Models and calibration

Direction uses the frozen v0 LightGBM classifier architecture:

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

Magnitude uses one LightGBM quantile regressor per horizon:

- objective `quantile`
- alpha `0.50`
- otherwise the same tree capacity, regularization, deterministic seed, and thread settings as direction

Quantile loss directly matches the primary MAE metric and avoids tail domination.

Retain session-weighted top-label correctness calibration. Use isotonic regression when the existing sufficiency checks pass; retain the frozen Platt fallback. Fit a separate confidence calibrator per horizon. Do not calibrate magnitude with final-test outcomes. Report magnitude calibration by predicted-magnitude decile: predicted median, realized median, count, and monotonicity. Calibration rows must not enter either base model’s training set.

## Candidate ladder

The lever order is: reformulate magnitude first, then add audited cross-asset features, and defer the per-stock extension to v2. V1-A is target-only, V1-B is spot/basis, and V1-C is production. V1-C is the pre-registered production candidate. A and B diagnose whether any recovery comes from target formulation or new information; they do not create a post-results model-selection search.

The exact machine-readable ordered manifests are in [feature_manifest.json](feature_manifest.json).

| Candidate | Exact composition | Role | Feature count |
|---|---|---|---:|
| V1-A | The v0 feature list from `features/intraday_prediction.py` plus the reformed magnitude target; no new features | Target-only diagnostic | 42 |
| V1-B | V1-A minus legacy `basis`, `basis_chg_30m`, plus the 8 NIFTY spot features and 6 reconstructed spot–futures basis features in section 3.4 | Spot/basis diagnostic | 54 |
| V1-C | V1-B plus the 10 BANKNIFTY-relative features and 7 lagged VIX features in section 3.4 | Production candidate | 71 |

## Data sources and alignment

### Source audit

| Source | Observed schema/range | Decision |
|---|---|---|
| `indices_minute/BANKNIFTY_minute.csv` | `date,open,high,low,close,volume`; only 6,367 rows from 2026-04-27 to 2026-05-25 | Reject for v1 development |
| `indices_minute/NIFTY_minute.csv` | Same schema and same 2026-only range | Reject |
| `nifty_intraday/NIFTY 50_minute.csv` | 1,062,380 rows; ISO timestamp plus OHLCV; history from 2015 | Use as NIFTY spot |
| `banknifty_intraday/bank-nifty-1m-data.csv` | 1,046,599 rows; `Instrument,Date,Time,Open,High,Low,Close`; `DD-MM-YYYY` dates | Use after normalization |
| `nifty_intraday/INDIA VIX_day.csv` | 4,291 daily rows; mixed date/date-time strings; OHLCV | Use previous completed source day only |
| `nifty500/*.csv` | 536 files; inspected examples have minute OHLCV | Defer |

The long NIFTY and BANKNIFTY minute sources match approximately 99.99% of cutoff-safe C1 timestamps. BANKNIFTY contains 3,690 duplicated timestamps; the duplicates inspected were value-identical. Both minute sources contain non-standard-session timestamps. VIX mixes date-only and date-time text. All timestamps are timezone-naive. Minute-bar labels and feed availability semantics are not documented.

Create one deep external-data module with this interface:

```text
load_intraday_external_features(decision_rows, cutoff, mode) -> aligned feature frame + audit
```

Its adapters own schema parsing, normalization, duplication policy, session filtering, lagging, missingness, and source auditing. Callers must not perform ad hoc CSV joins.

### Global alignment contract

For a futures decision timestamp (t):

- Treat timestamps as naive Asia/Kolkata exchange-local time. Do not timezone-convert.
- Restrict standard-session minute sources to `09:15–15:29`.
- External minute information is usable only through (t-1) minute.
- Require an exact external bar at (t-1); do not forward-fill an older intraday value.
- Return windows require exact same-session endpoints. Rolling volatility requires a contiguous window.
- Basis uses both futures and spot at (t-1), avoiding asynchronous (F_t/S_{t-1}).
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

### Exact new features

Notation:

- (F^-_t=F_{t-1})
- (S^-_t=S_{t-1}), from long-history NIFTY spot
- (K^-_t=K_{t-1}), from BANKNIFTY
- (L_k(X,t)=\log(X_t/X_{t-k}))
- All intraday windows are same-session exact-timestamp windows.

#### NIFTY spot

Source: `nifty_intraday/NIFTY 50_minute.csv`

| Feature | Formula |
|---|---|
| `spot_log_ret_1m` | (L_1(S^-,t)) |
| `spot_log_ret_5m` | (L_5(S^-,t)) |
| `spot_log_ret_15m` | (L_{15}(S^-,t)) |
| `spot_log_ret_30m` | (L_{30}(S^-,t)) |
| `spot_log_ret_60m` | (L_{60}(S^-,t)) |
| `spot_rv_15m` | std of the last 15 contiguous one-minute spot returns, annualized with `sqrt(252*375)` |
| `spot_rv_60m` | Same over 60 minutes |
| `spot_missing` | 1 when the exact (t-1) bar is absent, otherwise 0 |

Causal justification: all values end one completed external minute before the decision.

#### Reconstructed spot–futures basis

Sources: cutoff-safe C1 futures and long-history NIFTY spot.

Legacy `basis` and `basis_chg_30m` are removed from V1-B/V1-C to avoid retaining two differently aligned basis definitions.

\[
B_t=10{,}000\left(\frac{F^-_t}{S^-_t}-1\right)
\]

| Feature | Formula |
|---|---|
| `basis_bps_l1` | (B_t) |
| `basis_delta_5m` | (B_t-B_{t-5}) |
| `basis_delta_15m` | (B_t-B_{t-15}) |
| `basis_delta_30m` | (B_t-B_{t-30}) |
| `basis_z_60m` | z-score of (B_t) against the same-session trailing 60-minute window ending at (t-1), minimum 30 observations |
| `basis_missing` | 1 if either synchronized price is absent |

No theoretical carry adjustment is allowed because expiry and funding inputs are not present. This is an observed basis signal, not “fair-value basis.”

#### BANKNIFTY relative behavior

Source: `banknifty_intraday/bank-nifty-1m-data.csv`

| Feature | Formula |
|---|---|
| `bank_log_ret_5m` | (L_5(K^-,t)) |
| `bank_log_ret_15m` | (L_{15}(K^-,t)) |
| `bank_log_ret_30m` | (L_{30}(K^-,t)) |
| `bank_log_ret_60m` | (L_{60}(K^-,t)) |
| `bank_rel_ret_5m` | `bank_log_ret_5m - spot_log_ret_5m` |
| `bank_rel_ret_15m` | Same at 15 minutes |
| `bank_rel_ret_30m` | Same at 30 minutes |
| `bank_rel_ret_60m` | Same at 60 minutes |
| `bank_spot_rv_ratio_30m` | \(\log((RV^{bank}_{30}+\epsilon)/(RV^{spot}_{30}+\epsilon))\), \(\epsilon=10^{-12}\) |
| `bank_missing` | 1 when exact (t-1) BANKNIFTY data is absent |

These features measure whether the high-beta banking complex is leading or lagging the broad index without using future bars.

#### Lagged VIX

Source: `nifty_intraday/INDIA VIX_day.csv`

For futures trade date (d), select the latest VIX source date strictly less than (d). Same-day daily OHLC is prohibited because its high, low, and close are not known intraday.

| Feature | Formula |
|---|---|
| `vix_close_l1d` | Previous completed VIX close |
| `vix_log_ret_1d` | Log change between the previous two completed VIX closes |
| `vix_log_ret_5d` | Five-observation log change ending on the previous completed VIX day |
| `vix_z20` | Previous close z-score against the preceding 20 completed observations |
| `vix_range_l1d` | `(previous high - previous low) / previous close` |
| `vix_stale_calendar_days` | (d-\text{selected VIX date}) |
| `vix_missing` | 1 if no acceptable prior value exists |

A VIX value older than five calendar days is treated as missing. Weekend and holiday reuse within that limit remains observable through `vix_stale_calendar_days`.

## Development folds and embargoes

Retain v0 folds 1–4 and add one pre-2025 fold. Each transition keeps a seven-calendar-day embargo, and label-end purging remains horizon-specific. 2025H1 is excluded from development evaluation because its outcomes have already informed this design.

| Fold | Train | Calibration | OOS | Embargoes |
|---|---|---|---|---|
| 1 | 2021-01-01 → 2022-06-23 | 2022-07-01 → 2022-09-23 | 2022-10-01 → 2022-12-31 | 2022-06-24 → 2022-06-30; 2022-09-24 → 2022-09-30 |
| 2 | 2021-01-01 → 2022-12-24 | 2023-01-01 → 2023-03-24 | 2023-04-01 → 2023-06-30 | 2022-12-25 → 2022-12-31; 2023-03-25 → 2023-03-31 |
| 3 | 2021-01-01 → 2023-06-23 | 2023-07-01 → 2023-09-23 | 2023-10-01 → 2023-12-31 | 2023-06-24 → 2023-06-30; 2023-09-24 → 2023-09-30 |
| 4 | 2021-01-01 → 2023-12-24 | 2024-01-01 → 2024-03-24 | 2024-04-01 → 2024-06-30 | 2023-12-25 → 2023-12-31; 2024-03-25 → 2024-03-31 |
| 5 | 2021-01-01 → 2024-06-23 | 2024-07-01 → 2024-09-23 | 2024-10-01 → 2024-12-31 | 2024-06-24 → 2024-06-30; 2024-09-24 → 2024-09-30 |

## Fixed baselines

All baseline parameters are learned only from each fold’s training block.

### Direction

1. random balanced accuracy: `1/3`
2. always-majority class
3. last-five-minute sign
4. multinomial linear model
5. frozen v0 LightGBM architecture retrained on identical rows

### Magnitude

1. zero magnitude, sanity baseline
2. unconditional training median of `abs(return_bps)`
3. scale-only baseline: \(\operatorname{median}_{train}(u_h)s_{h,t}\)
4. 30-minute time-bucket scale baseline: \(\operatorname{median}_{train}(u_h\mid time\ bucket)s_{h,t}\), with global-median fallback

“Best baseline” means the lowest OOS MAE among this fixed set. No baseline is selected using calibration or final outcomes.

## Evaluation and pre-registered gates

Report for every fold, horizon, and candidate the frozen direction, confidence, and magnitude metrics from sections 5.3 and 5.4 of the design spec. Metrics are calculated on all valid minute decisions, for v0 comparability, and on non-overlapping decisions anchored at 09:30 and every H minutes thereafter. Daily IC uses the full minute sample because H60 produces too few non-overlapping observations per day for a stable within-day rank. Uncertainty uses a 2,000-draw paired session-block bootstrap.

### Development pass standards

A horizon passes independently. Failure of one output cannot be offset by another output’s success.

| Output | Development pass standard |
|---|---|
| Direction | Pooled OOF balanced accuracy ≥ `0.370`; macro AUC ≥ `0.540`; BA above `1/3` in at least 4/5 folds |
| Direction strengthening | V1-C improves pooled BA over the frozen v0 comparator by ≥ `0.003` at one horizon and degrades the other by no more than `0.002` |
| Confidence H15 | ECE ≤ `0.030`; decile-accuracy Spearman ≥ `0.80`; top-minus-bottom decile ≥ `0.08` |
| Confidence H60 | ECE ≤ `0.050`; decile-accuracy Spearman ≥ `0.80`; top-minus-bottom decile ≥ `0.08` |
| Magnitude | Relative MAE skill ≥ `2%`; positive skill in at least 4/5 folds; daily IC mean ≥ `0.05`; positive-day fraction ≥ `0.55`; non-overlapping MAE skill ≥ `1%` |

Relative MAE skill is \(1-MAE_{model}/MAE_{best\ baseline}\). Magnitude calibration is reported by predicted-magnitude decile with predicted median, realized median, count, and monotonicity.

### Sealed H2 final pass standards

| Output | Sealed H2 success standard |
|---|---|
| Direction | BA ≥ `0.370`, macro AUC ≥ `0.540`, and BA improvement over frozen v0 comparator ≥ `0.003` |
| Confidence H15 | ECE ≤ `0.030`, decile Spearman ≥ `0.80`, top-minus-bottom decile ≥ `0.08` |
| Confidence H60 | ECE ≤ `0.050`, decile Spearman ≥ `0.80`, top-minus-bottom decile ≥ `0.08` |
| Magnitude | MAE skill ≥ `2%`; paired session-bootstrap 95% lower bound for skill > `0`; daily IC mean ≥ `0.05`; positive-day fraction ≥ `0.55`; non-overlapping MAE skill > `0` |

## Kill criteria

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

## Final-test protocol

The 2025H1 test is declared **BURNED**. It cannot be called untouched again and must not determine v1 promotion. It is explicitly reclassified as known historical data for final training/calibration, and it must not receive a v1 test score.

After the v1 code, feature manifest, thresholds, and hashes are frozen:

- **Train:** 2021-01-01 through 2025-03-24
- **Embargo:** 2025-03-25 through 2025-03-31
- **Calibration:** 2025-04-01 through 2025-06-23
- **Embargo:** 2025-06-24 through 2025-06-30
- **New one-shot sealed test:** 2025-07-01 through 2025-12-31

The H2 test is opened once, after:

- registration and source hashes are frozen
- all development gates have passed
- the exact V1-C candidate is fixed
- no final-test values have been inspected
- the access/unseal is recorded in the manifest

The frozen v0 comparator and V1-C may be evaluated together as one pre-registered final protocol.

## Frozen file hashes

These hashes freeze the design spec and both machine-readable protocol artifacts. `registration.md` is not self-hashed.

| File | SHA-256 |
|---|---|
| `DESIGN_SPEC_SOL.md` | `404926d62b43e1def9cb382ae71d7ee09c4ce7f0ba70931d7b1576668569e4dc` |
| `feature_manifest.json` | `973c17495edc2a9f860e0d26f71e8b2fd1214e348b89d1a70acb11a472a6ad89` |
| `evaluation_protocol.json` | `a5690c743112d7e28ad123a85db8e8ce51d9eea2856b4203061a2d8e4245e347` |
