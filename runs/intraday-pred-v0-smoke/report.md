# Intraday Prediction V0

Rows: 22500 across 60 sessions.

## Run metrics

| Block | Status | Direction accuracy | Balanced accuracy | Magnitude MAE (bps) | Calibrator |
|---|---:|---:|---:|---:|---|
| smoke_h15 | ok | 0.3763 | 0.3615 | 7.2690 | platt |
| smoke_h60 | ok | 0.3821 | 0.3757 | 14.2210 | platt |

V0 uses cutoff-safe C1-derived futures features and the frozen close-to-close targets. Cross-asset features and PSI diagnostics are omitted by design.
