# evaluation

Blind scoring of the causal detectors on the 2025 test year. This is the only project that reads
`ground_truth.parquet`. It has its own uv environment.

## Protocol

| Step | Data | Rule |
|---|---|---|
| Feature parameters, including ERA5 | Train 2022–2023 | Fitted in `signal-processing`, no labels |
| Model training | Validation 2024 labels | Gradient boosting on the `sp_*` features + station id |
| Thresholds | Validation 2024 | Out-of-fold scores; best F1 under a 1 % false-alarm cap |
| Scoring | Test 2025 | The frozen model is applied; test labels are read once, after it is saved |

Train has no attacks, so the supervised model can only learn from validation. The score files contain
2025 rows only, so validation rows are never presented as out-of-sample.

### Two operating modes

| Mode | Features | `decision_delay_h` | Use |
|---|---|---:|---|
| Real-time | Without ERA5 | 0 | Immediate verdict |
| Delayed confirmation | With ERA5 | 120 | Confirms or clears the verdict once ERA5 is published |

The 120 h comes from the ERA5 publication latency in [era5/README.md](../era5/README.md). Each mode has
its own model, thresholds, score file and manifest.

### ERA5 and 2025

ERA5 features carry information about the test period, so these checks exist specifically for them:

- **ERA5 fit: train only.** `signal-processing/scripts/era5_leak_check.py` removes every row measured
  from 2024 onwards from both inputs, refits, and requires exact equality with the frozen ERA5
  parameters. Result: identical
  ([report](../signal-processing/outputs/report/era5_leak_check.json)).
- **Model and thresholds: validation only.** `build_detector` receives only the validation frame and
  refuses any other measured year. Only validation rows of the truth file are loaded for it.
- **Tested.** `test_2025_features_and_labels_cannot_change_the_detector` overwrites the 2025 features,
  inverts the 2025 labels, and requires the same thresholds and model predictions.

## Results (test 2025)

<!-- FACTS:START -->
Test rows scored: 52,525, of which 632 attacked.
<!-- FACTS:END -->

<!-- MAIN:START -->
| Detector | Mode | Precision | Recall | F1 | False-alarm rate | PR-AUC |
|---|---|---:|---:|---:|---:|---:|
| Rules | real-time | 0.637 | 0.335 | 0.439 | 0.23 % | 0.303 |
| Model | real-time | 0.650 | 0.440 | 0.525 | 0.29 % | 0.513 |
| Model + ERA5 | delayed confirmation | 0.697 | 0.598 | 0.644 | 0.32 % | 0.713 |
<!-- MAIN:END -->

Per family, as hourly recall / events detected. An event counts as detected if at least one of its hours
raises an alarm:

<!-- FAMILY:START -->
| Detector | Spike | Bias | Drift | Replay | Stealth |
|---|---:|---:|---:|---:|---:|
| Rules | 0.680 / 59 of 59 | 0.000 / 0 of 4 | 0.000 / 0 of 3 | 0.648 / 6 of 7 | 0.000 / 0 of 2 |
| Model | 0.990 / 59 of 59 | 0.168 / 1 of 4 | 0.109 / 1 of 3 | 0.639 / 5 of 7 | 0.000 / 0 of 2 |
| Model + ERA5 | 1.000 / 59 of 59 | 0.319 / 3 of 4 | 0.707 / 2 of 3 | 0.607 / 5 of 7 | 0.000 / 0 of 2 |
<!-- FAMILY:END -->

**Read the slow families with care.** Bias, drift and stealth have only 2–4 test events each and replay
has 7, so one event moves a detection rate by 14–50 points. Spike has 59. Stealth is not detected by any
model hour in this test set. The ERA5 row is not a real-time result: it assumes a decision made about
five days late.

These tables are generated from `results/*/causal_detector_results.json` by `make_results_table.py`.

## Reproduce

Run from `evaluation/`. `$G` is the combined export directory and `$SP` is `../signal-processing`.

```bash
uv sync --locked
uv run python causal_detector_eval.py evaluate --features $SP/outputs/combined/signal_features.parquet --truth $G/ground_truth.parquet --params $SP/params/frozen_params.json --out results/realtime --model-out models/causal_gbm.joblib
uv run python causal_detector_eval.py evaluate --features $SP/outputs/combined_era5/signal_features.parquet --truth $G/ground_truth.parquet --params $SP/params/frozen_params.json --out results/delayed --model-out models/causal_gbm_era5.joblib
uv run python causal_detector_eval.py score --features $SP/outputs/combined/signal_features.parquet --model models/causal_gbm.joblib --sums models/models.sha256 --out outputs/realtime --mode real-time --attack-set-id combined-seed14-ff478b38 --decision-delay-h 0
uv run python causal_detector_eval.py score --features $SP/outputs/combined_era5/signal_features.parquet --model models/causal_gbm_era5.joblib --sums models/models.sha256 --out outputs/delayed --mode delayed-confirmation --attack-set-id combined-seed14-ff478b38 --decision-delay-h 120
uv run python make_results_table.py
```

`score` reads no labels and refuses a model whose SHA-256 differs from `models/models.sha256`.
`ai_scores.parquet` has the columns `station, time, ai_score, ai_reason`. Its `manifest.json` records the
file name, SHA-256, threshold, model version, attack-set id, `decision_delay_h`, and the rule thresholds
that `trust-decision` applies.

`demo_summary.py` also reads labels, solely to print the end-to-end demo summary.
