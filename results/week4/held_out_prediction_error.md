# Held-out prediction error (Adaptive predictor)

Predicted runtime is `predicted_adaptive[(class, tier)]`, the median of the calibration pool for that cell -- the same value Adaptive/DP/Admission-Controlled DP use. Actual runtime is drawn from `concurrency_contention_heldout.csv`, a separately collected round (see `load_heldout_pool` in simulate_multi_query_scheduling.py). MAE/MAPE are the median absolute error / median absolute percentage error across held-out samples in each cell.

| Class | Tier | n | Predicted (ms) | MAE (ms) | MAPE (%) | Genuinely held out |
|---|---|---:|---:|---:|---:|---|
| short | low | 8 | 286.7 | 23.0 | 8.0 | yes |
| medium | low | 15 | 248.4 | 8.4 | 3.5 | no (fallback to calibration pool) |
| long | low | 22 | 879.4 | 49.6 | 5.7 | no (fallback to calibration pool) |
| short | moderate | 40 | 1630.6 | 789.1 | 38.2 | yes |
| medium | moderate | 32 | 2487.1 | 309.3 | 12.9 | yes |
| long | moderate | 24 | 2637.4 | 445.0 | 14.4 | yes |
| short | high | 48 | 5771.7 | 391.6 | 6.4 | yes |
| medium | high | 40 | 5694.8 | 473.0 | 7.8 | yes |
| long | high | 40 | 5963.1 | 443.7 | 6.9 | yes |

## Per-tier aggregate (genuinely held-out cells only, pooled)

| Tier | n | MAE (ms) | MAPE (%) |
|---|---:|---:|---:|
| low | 8 | 23.0 | 8.0 |
| moderate | 96 | 746.2 | 30.6 |
| high | 128 | 448.2 | 7.2 |

Fallback cells (not genuinely held out -- `concurrency_contention_test.py` only cycles the first query class at concurrency=1, so held-out low-tier data doesn't cover every class; these resample the calibration pool instead and are excluded from the aggregate above): [('medium', 'low'), ('long', 'low')]
