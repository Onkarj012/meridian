# Intraday prediction — complete results packet (2026-07-19)

> All numbers below are **retrospective descriptive analysis** computed from the committed run artifacts. Confidence-filter cutoffs were examined after outcomes were known — they describe the data, they are **not** a validated trading policy (Sol verdict, `registrations/intraday-pred-v1/verdict-2026-07-19.md`).

The model predicts, every minute, whether NIFTY futures will be UP / FLAT / DOWN after 15 minutes (h15) and 60 minutes (h60), how confident it is, and (v0 only) a price target.

## v0 — final honest test (Jan–Jun 2025, data the model had never seen)

### 15-minute horizon

- Predictions scored: **43,920**
- Raw accuracy (all predictions): **37.4%** (random guessing = 33.3%)
- Balanced accuracy (each class counted equally): **37.0%**
- Accuracy when model actually calls UP or DOWN (32,373 calls): **39.0%**

Confidence-filtered accuracy (keep only the minutes the model is most sure about):

| Kept | Predictions | Accuracy |
|---|---:|---:|
| All | 43,920 | 37.4% |
| Top 50% confidence | 30,239 | 38.8% |
| Top 25% confidence | 16,830 | 40.8% |
| Top 10% confidence | 7,752 | 43.3% |

Prediction vs reality (rows = what happened, columns = what model said):

| Actual \ Predicted | DOWN | FLAT | UP |
|---|---:|---:|---:|
| DOWN | 5,708 | 3,883 | 6,251 |
| FLAT | 3,600 | 3,825 | 4,347 |
| UP | 5,565 | 3,839 | 6,902 |

Price prediction: model average miss **21.55 index points** vs **21.56** for simply predicting "no change". Advantage only 0.015 points (~0.07%) — economically nothing.

### 60-minute horizon

- Predictions scored: **38,430**
- Raw accuracy (all predictions): **37.0%** (random guessing = 33.3%)
- Balanced accuracy (each class counted equally): **36.6%**
- Accuracy when model actually calls UP or DOWN (28,531 calls): **38.2%**

Confidence-filtered accuracy (keep only the minutes the model is most sure about):

| Kept | Predictions | Accuracy |
|---|---:|---:|
| All | 38,430 | 37.0% |
| Top 50% confidence | 35,436 | 37.4% |
| Top 25% confidence | 35,436 | 37.4% |
| Top 10% confidence | 35,436 | 37.4% |

Prediction vs reality (rows = what happened, columns = what model said):

| Actual \ Predicted | DOWN | FLAT | UP |
|---|---:|---:|---:|
| DOWN | 5,834 | 2,960 | 4,810 |
| FLAT | 3,752 | 3,345 | 3,656 |
| UP | 5,422 | 3,594 | 5,057 |

Price prediction: model average miss **42.39 index points** vs **42.38** for simply predicting "no change". Model is WORSE than doing nothing.

Note: at the 60-minute horizon the calibrated confidence is nearly constant, so confidence filtering barely separates anything there in this test.

## v1 — development evaluation (5 walk-forward folds, 2022–2024; candidate V1-C)

### 15-minute horizon

- Predictions scored: **108,736**
- Raw accuracy (all predictions): **36.7%** (random guessing = 33.3%)
- Balanced accuracy (each class counted equally): **37.0%**
- Accuracy when model actually calls UP or DOWN (70,632 calls): **39.2%**

Confidence-filtered accuracy (keep only the minutes the model is most sure about):

| Kept | Predictions | Accuracy |
|---|---:|---:|
| All | 108,736 | 36.7% |
| Top 50% confidence | 55,816 | 38.5% |
| Top 25% confidence | 28,218 | 39.6% |
| Top 10% confidence | 12,717 | 42.6% |

Prediction vs reality (rows = what happened, columns = what model said):

| Actual \ Predicted | DOWN | FLAT | UP |
|---|---:|---:|---:|
| DOWN | 11,035 | 12,599 | 14,425 |
| FLAT | 7,304 | 12,222 | 9,819 |
| UP | 11,410 | 13,283 | 16,639 |

### 60-minute horizon

- Predictions scored: **95,072**
- Raw accuracy (all predictions): **37.6%** (random guessing = 33.3%)
- Balanced accuracy (each class counted equally): **36.5%**
- Accuracy when model actually calls UP or DOWN (71,302 calls): **39.7%**

Confidence-filtered accuracy (keep only the minutes the model is most sure about):

| Kept | Predictions | Accuracy |
|---|---:|---:|
| All | 95,072 | 37.6% |
| Top 50% confidence | 51,111 | 38.5% |
| Top 25% confidence | 24,983 | 42.3% |
| Top 10% confidence | 10,479 | 48.5% |

Prediction vs reality (rows = what happened, columns = what model said):

| Actual \ Predicted | DOWN | FLAT | UP |
|---|---:|---:|---:|
| DOWN | 9,892 | 7,805 | 15,559 |
| FLAT | 6,050 | 7,402 | 10,260 |
| UP | 11,102 | 8,563 | 18,439 |

## Smoothed accuracy

Not yet measurable: no smoothed-input or smoothed-target model has been built. Sol ruled the smoothed-direction idea a legitimate, registrable study (v1.1-smooth) — see the verdict and `registrations/intraday-pred-v1_1-smooth/registration.md`. Numbers will exist after it runs on newly accruing market sessions.

## Bottom line

- Direction has a small but real edge: ~37% vs 33.3% random, rising to ~41–49% on the most-confident minutes.
- Confidence ordering is real descriptively but failed its stability gate (worst-fold behavior), so it is not yet deployable.
- Price prediction is dead: never better than "predict no change" by a meaningful amount, twice, under two different formulations.
- v1 external features (spot / BANKNIFTY / VIX) added nothing; v1 was killed at its development gates and the kill is ratified.
