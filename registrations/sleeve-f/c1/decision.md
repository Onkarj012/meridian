# Sleeve F — Campaign 1 disposition

**Status:** `KILLED_AT_WF`
**Decision date:** 2026-07-16
**Principal acknowledgement:** Onkar approved the C1 kill record and approved C2 supersession under the recommended withdrawal decision (Option 2). The holdout remains sealed.

## Registration and artifact identity

- C1/C2 registration freeze commit: `1f387b8`.
- Candidate-B skip precedent commit: `dbdaa83`.
- Pre-work HEAD: `738679c`.
- Provenance commit: `4b9d0a12edf8a1953593e2f75fa22c494ae67f73`.
- Artifact commit: `82533fb18856adcac2fa4f1a50ac5c861fb2e10b`.
- Artifact directory: `runs/sleeve-f-c1-wf/`.
- Manifest: `runs/sleeve-f-c1-wf/MANIFEST.sha256.json`.
- Manifest SHA-256: `9a3704b4a87331c17b41f9867b116dfa683925f78c9434a2b20e067db630c7b9`.
- Matrix SHA-256: `a534993df198aee0dbef3f01c961e04df0ba4e58a02729a8e8369d37ac333660`.
- Manifest inventory: 578 outcome files, 221979029 bytes; manifest itself is excluded from that inventory.
- `dsr_k`: 95.
- `holdout_accessed`: false.

### Provenance snapshot

- Branch: `feat/sleeve-f-phase1`.
- Dirty paths at snapshot: the six provenance files below plus untracked `runs/sleeve-f-c1-wf/`.
- Run directory snapshot: 578 files, 221979029 bytes.

| File | SHA-256 |
|---|---|
| `evidence/c1_replay.py` | `4753373e27ab74f1e39aa48af889c00b7fc7246137429850bdc969f6584db037` |
| `models/c1_baselines.py` | `5a3c073e30d7ae1740f2663adf8c3ec23785b4b6d19b380ebc27bdfe065a2c66` |
| `runs/sleeve-f-data-contract/checksums.txt` | `8c5f61b0faf12a4f1c12b58378c2954f736edc49783a7baafbcc707055322c3c` |
| `runs/sleeve-f-data-contract/contract_calendar.csv` | `405ad15990e5baae690f71c44e93309b01794e5cc458076aa306ba713d179655` |
| `runs/sleeve-f-data-contract/coverage_report.md` | `2a8a41edee9c2a108f1433960de883c9c8ed3e6c961011df43eba7e024d68d46` |
| `runs/sleeve-f-data-contract/expiries.csv` | `fced83a474ed01cc3389ac6df726cd074de1a0fb7ff3895fb7b884dabe633286` |

## Frozen WF gate disposition

All values below are copied from `runs/sleeve-f-c1-wf/summary.json` and `report.md`. `PASS` is the recorded gate result, not a new judgment.

| Gate | Registered criterion | Candidate A | Candidate C |
|---|---|---:|---:|
| WF Sharpe | `>= 0.75` | `-1.2677500695655293` FAIL | `-1.3409746948842753` FAIL |
| Mean daily PnL (bps) | `> 0` | `-1.5785226568103585` FAIL | `-0.9870274578864339` FAIL |
| Mean daily PnL MBB 20d × 10,000 CI95 | interval | `[-2.7811521968987445, -0.3668075364701985]` | `[-1.725016969847228, -0.2544100268445326]` |
| 5 bps stress PnL | `> 0` | `-4.77884852911716` FAIL | `-2.8062373191641776` FAIL |
| 5 bps stress Sharpe | `> 0` | `-3.905939842606519` FAIL | `-3.7650163534755707` FAIL |
| Matched baselines paired | required | `false` FAIL | `false` FAIL |
| Quarter/era/bucket domination | registered but no measurable cap | `REPORT-ONLY` | `REPORT-ONLY` |
| WF trade minimum | `>= 300` | `865.0` PASS | `490.0` PASS |
| Operationally executable | required | `true` PASS | `true` PASS |
| 6a WF | all enforced legs | `false` FAIL | `false` FAIL |
| 6b WF preview ΔSharpe | `>= 0.25` | `-0.4191401795101646` FAIL | `-0.08741303164930625` FAIL |
| 6b WF preview paired MBB CI-low | `> 0` | `-2.1482554384388584` FAIL | `-0.37178878851348557` FAIL |

The stitched metric `trade_days` was 504 for A and 349 for C. The hard trade-minimum values above are the gate values recorded in `six_a_wf`.

### Paired baseline comparison

| Candidate | Baseline | Baseline Sharpe | Candidate Sharpe | ΔSharpe | Paired MBB CI95 |
|---|---|---:|---:|---:|---:|
| A | random_entry | `-1.0682542332327045` | `-1.2677500695655293` | `-0.19949583633282475` | `[-1.6062539006077057, 1.0510394898016304]` |
| A | time_of_day | `-0.8818234208606807` | `-1.2677500695655293` | `-0.3859266487048486` | `[-2.1482554384388584, 1.077993715187409]` |
| A | unconditional_long | `-1.3081597187537823` | `-1.2677500695655293` | `0.04040964918825307` | `[-1.7061546907906344, 1.3305886361312427]` |
| A | volatility | `-0.8486098900553647` | `-1.2677500695655293` | `-0.4191401795101646` | `[-1.8287835738290934, 0.974660315286006]` |
| C | random_entry | `-1.3651041714936807` | `-1.3409746948842753` | `0.024129476609405343` | `[-0.15407110617504144, 0.22512095988909103]` |
| C | time_of_day | `-1.535244552009302` | `-1.3409746948842753` | `0.19426985712502676` | `[-0.13896565217434315, 0.5033188736600133]` |
| C | unconditional_long | `-1.261152084614575` | `-1.3409746948842753` | `-0.07982261026970039` | `[-0.34844176593845005, 0.24202530230609046]` |
| C | volatility | `-1.253561663234969` | `-1.3409746948842753` | `-0.08741303164930625` | `[-0.37178878851348557, 0.2939707972914166]` |

## Decision record

- `selected_candidate: none`.
- Candidate B status: `SKIPPED`; no `--b-artifact` was supplied, and the registered minute windows remained an open governance item.
- §6c was mechanically triggered because no candidate passed the registered WF gates. No judgmental override was used.
- C1 holdout accessed: `false`; holdout dates are 2025-07-01 → 2026-06-30. The holdout remains unopened and off-limits.
- C1 is closed. Prohibited continuations: C1 retuning, cost-only rescue, feature additions on the same skeleton, and opening the C1 holdout.
- The C1 decision tree technically triggers C2 Branch B. C2 execution is separately governed by the supersession record.

## Post-hoc autopsy

This is post-hoc autopsy evidence, not an additional gate or a tuning input:

- Gross PnL was negative before costs: A `−Rs1,405` gross; C `−Rs15,453` gross.
- Hit rate was approximately 44–45%.
- TIMEOUT exits were approximately 72–79% of trades.
- No short trades occurred in any fold.
- Losses occurred in every calendar year.
- Threshold instability was uncorrelated with fold PnL.
- Baselines were positive in only 6–7 of 18 folds.

Source identity for the autopsy is the frozen C1 WF artifact set at commit `82533fb` and manifest `9a3704b4a87331c17b41f9867b116dfa683925f78c9434a2b20e067db630c7b9`; no holdout artifact was accessed.

## Multiplicity and sequencing

- Declared ledger: `k = 101` for C1 `k = 95` plus the six registered C2 configurations.
- Executed-outcome ledger: `k = 95`; only C1 WF outcomes were inspected.
- Any replacement campaign starts after declared `k = 101`.
