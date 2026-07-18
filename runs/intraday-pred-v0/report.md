# Intraday Prediction V0

Rows: 508918 across 1359 sessions.

## Run metrics

| Block | Status | Direction accuracy | Balanced accuracy | Magnitude MAE (bps) | Calibrator |
|---|---:|---:|---:|---:|---|
| fold_1_h15 | ok | 0.3914 | 0.3688 | 7.9820 | isotonic |
| fold_1_h60 | ok | 0.4115 | 0.3793 | 15.2833 | isotonic |
| fold_2_h15 | ok | 0.3822 | 0.3750 | 5.9866 | isotonic |
| fold_2_h60 | ok | 0.4052 | 0.3658 | 11.8018 | isotonic |
| fold_3_h15 | ok | 0.3606 | 0.3620 | 6.2322 | isotonic |
| fold_3_h60 | ok | 0.3634 | 0.3437 | 12.2332 | isotonic |
| fold_4_h15 | ok | 0.3678 | 0.3676 | 9.4515 | isotonic |
| fold_4_h60 | ok | 0.3514 | 0.3463 | 19.2992 | isotonic |

V0 uses cutoff-safe C1-derived futures features and the frozen close-to-close targets. Cross-asset features and PSI diagnostics are omitted by design.
