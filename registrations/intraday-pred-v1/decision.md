# Intraday-pred v1 — development disposition

**Status:** `KILLED_AT_DEV_GATES`
**Decision date:** 2026-07-19
**Principal acknowledgement:** RATIFIED_BY_DELEGATION (2026-07-19). The principal delegated the verdict to the design authority: "whatever may be the case, discuss with Sol and get a verdict on it and drive a plan forward, either for this phase or the next phases, which one will improve the system." Sol ratified the kill without change to the disposition — see [verdict-2026-07-19.md](verdict-2026-07-19.md). This records delegation, not independent principal review of each metric.

## Registration and artifact identity

- Registration freeze commit: `41eeda5` (registration hash `d1f1a6bd4df400a6851f476dc1789f62840b9bcb1547c99e62f5cb86de9a1fa4`).
- Implementation-fix commit: `4a7f17c` (see "Voided first run" below).
- Evidence directory: `runs/intraday-pred-v1-dev2/` (463,168 rows, 1,237 sessions, seed `20260718`, 5 registered folds × 3 candidates × 2 horizons).
- Voided first run: `runs/intraday-pred-v1-dev/` — retained only as provenance for the two implementation bugs; its metrics are not gate evidence.
- Sealed 2025H2 test: **not accessed**. `holdout_accessed: false`.

## Voided first run

The first dev execution diverged from the registered protocol through two implementation bugs, both fixed in `4a7f17c` before the evidence run:

1. The v1 classifier received the normalized-magnitude label whose NaN filter silently dropped ~0.9% of training rows (every session-opening minute) and shifted class weights. Fixed to `abs(return_bps)`; the corrected classifier reproduces the in-run frozen-v0 baseline exactly.
2. The frozen-v0 comparator was fit on each candidate's own feature frame instead of the registered v0 42-feature list (the V1-A manifest). Fixed by threading a single V1-A comparator frame through the runner.

Verification that both fixes are correct: in the corrected run, V1-A's pooled balanced accuracy equals its frozen-v0 comparator to four decimals at both horizons (ΔBA `+0.0000`), as it must by construction (identical architecture, features, and training rows).

## Frozen dev gate disposition — V1-C (registered production candidate)

All values are computed from `runs/intraday-pred-v1-dev2/candidate_ablation.json`. Pooled BA uses summed OOS confusion matrices across the five registered folds. `PASS`/`FAIL` is the recorded gate result against `evaluation_protocol.json` `development_gates`.

### Direction

| Gate | Criterion | H15 | H60 |
|---|---|---:|---:|
| Pooled balanced accuracy | `>= 0.370` | `0.3697` FAIL | `0.3645` FAIL |
| Strengthening over frozen v0 comparator | `>= +0.003` (≥1 horizon; other degrades `<= 0.002`) | `-0.0021` FAIL | `-0.0005` FAIL |
| Folds with BA above random | `>= 4/5` | `5/5` PASS | `5/5` PASS |
| Mean macro OVR AUC | `>= 0.540` | `0.5433` PASS | `0.5421` PASS |

### Confidence

| Gate | Criterion | H15 | H60 |
|---|---|---:|---:|
| ECE (worst fold) | `<= 0.030` / `<= 0.050` | `0.0247` PASS | `0.0579` FAIL |
| Decile-accuracy Spearman (worst fold) | `>= 0.80` | `0.600` FAIL (mean `0.685`) | `0.273` FAIL (mean `0.539`) |
| Top-minus-bottom decile accuracy (mean) | `>= 0.08` | `0.0950` PASS | `0.1360` PASS |

### Magnitude

| Gate | Criterion | H15 | H60 |
|---|---|---:|---:|
| Relative MAE skill vs best fixed baseline | `>= 0.02` | mean `+0.0084` FAIL | mean `-0.0132` FAIL |
| Folds with positive skill | `>= 4/5` | `4/5` PASS | `0/5` FAIL |
| Non-overlapping MAE skill | `>= 0.01` | mean `+0.0066` FAIL | mean `-0.0118` FAIL |
| Daily IC mean | `>= 0.05` | `0.136` PASS | `0.067` PASS |
| Positive-IC day fraction | `>= 0.55` | `0.841` PASS | `0.589` PASS |

### Diagnostic candidates (report-only, not selectable)

V1-A (v0 features, reformed target): H15 pooled BA `0.3717`, ΔBA `+0.0000`. V1-B (spot/basis): H15 pooled BA `0.3732`, ΔBA `+0.0015`. Neither meets the strengthening gate, and the registration forbids post-results selection of a diagnostic candidate over the pre-committed V1-C.

## Disposition

Per the registration's independent, non-compensating gate structure and the design authority's post-hoc ruling:

1. **Direction:** the reformed target and external NIFTY/BANKNIFTY/VIX features add no measurable direction edge over the frozen v0 architecture (ΔBA ≈ 0 at both horizons). v1 direction is not adopted; the frozen v0 direction system remains the reference.
2. **Confidence:** recorded as failed. The 0.80 Spearman threshold was ambitious relative to v0's own observed instability, but a threshold cannot be revised after seeing results. Any recalibrated gate requires a new prospectively frozen study.
3. **Magnitude:** the H15 daily rank IC (`0.136`, positive on 84% of days) is recorded as a research finding. It does not constitute a pass — MAE skill and non-overlapping skill are mandatory co-gates — and re-scoping the output as a ranking signal post-results is prohibited. Learned magnitude for this information class is retired pending structurally new information.
4. **Sealed 2025H2 final test: remains sealed.** "All development gates have passed" is an explicit unseal precondition and is not met. No final-test values were inspected at any point.

Any v1.1 must be registered as a new prospective study with its own frozen thresholds; these development folds are burned for that purpose to the extent they informed this design.
