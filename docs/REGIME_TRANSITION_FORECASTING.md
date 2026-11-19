# Regime Transition Forecasting

## Purpose

This research module estimates how market-regime labels have transitioned
historically and projects a state distribution over future bar horizons. It
complements descriptive regime detection with a separate, testable forecasting
layer. It is not a signal generator.

## Method

- Fit a first-order Markov transition matrix from chronological historical labels.
- Apply additive (Dirichlet/Laplace) smoothing to avoid zero-probability cells.
- Record the training data fingerprint and last training timestamp.
- Project future state distributions using matrix powers.
- Report source transition counts, low-support reachable states and transition
  entropy. If the current state's row count is below the minimum, withhold the
  probability vector entirely.

The first-order Markov assumption is intentionally transparent and limited.
It may miss regime duration, structural breaks, macro events and latent drivers.

## Forecast journal

Regime forecasts store instrument, market type, timeframe, horizon, model ID,
dataset fingerprint, issue time, target time, data cutoff and the complete
state-probability vector. The data cutoff cannot exceed issue time. Forecasts
cannot resolve before their target horizon.

## Out-of-sample evaluation

Resolved categorical forecasts are scored using multiclass log loss,
multiclass Brier score and top-1 accuracy. Different instruments, market types,
timeframes, horizons and model versions are not pooled into a single metric.

## Safety and interpretation

- Transition probabilities are smoothed historical estimates, not calibrated
  real-world probabilities.
- Low-support states are flagged.
- Chronological walk-forward evaluation is required before relying on the model.
- No trade signal, strategy weight, position size or order instruction is emitted.
