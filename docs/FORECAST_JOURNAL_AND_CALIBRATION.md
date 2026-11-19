# Forecast Journal and Calibration

## Product role

ChartAnalyzer is a market-intelligence and predictive-research platform. Forecast
evaluation is a core research capability; generating a trading signal is optional
and downstream. This module provides deterministic forecast identity, point-in-time
leakage guards, delayed resolution and out-of-sample scoring.

## Forecast contract

Each forecast records:

- Canonical instrument, market type, timeframe and horizon in bars.
- Issue time, target time and the latest data timestamp used by the model.
- Model identifier/version and immutable dataset fingerprint.
- Market regime at issue time.
- Distributional return quantiles (10th, 50th, 90th percentile).
- Optional probability of a positive log return.

The data cutoff must not be later than issue time. Target time must be later than
issue time. Quantiles must be ordered and probability must be in [0, 1]. The
forecast ID is a stable SHA-256 digest of the full forecast payload.

## Resolution and metrics

A forecast cannot be resolved before its target horizon. Resolved records are
scored using:

- Median forecast MAE and RMSE.
- Central 80% interval coverage and average width.
- Pinball loss at q10, q50 and q90.
- Brier score for forecasts that supplied P(return > 0).
- Reliability bins, observed event frequency and expected calibration error.
- Segments by model, horizon, regime and instrument, with minimum sample flags.

A high interval coverage is not automatically good if the interval is excessively
wide. Coverage and width must be read together. Calibration bins always include
sample counts; sparse samples are not evidence of reliable probabilities.

## Research controls

- Only resolved out-of-sample forecasts are scored.
- Forecast payloads are immutable; actual outcomes are separate resolution records.
- Dataset fingerprints and model versions make comparisons auditable.
- Do not combine spot, futures, equities, different instruments or horizons without
  a documented comparability policy.
- Forecast probabilities are empirical estimates, not guarantees.
- No strategy weights, signal generation, trade sizing or order execution are changed.

## Integration boundary

This PR adds the evaluation contract and pure metrics. A later persistence/API
integration should store issued forecasts before target time, resolve them only
after the horizon closes, and expose calibration drift and rolling performance.
