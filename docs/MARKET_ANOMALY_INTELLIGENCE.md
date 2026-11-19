# Market Anomaly Intelligence

## Purpose

This module detects statistically unusual market observations from OHLCV
history. It is a market-intelligence diagnostic, not a signal generator or
a manipulation detector.

## Features

Each candle is compared with a trailing baseline that ends before that candle:

- Absolute log-return shock.
- High-low range expansion relative to close.
- Log-volume surprise or drought.
- Open-to-previous-close gap shock.
- Price shock without a corresponding volume surprise (price-volume dislocation).

The baseline uses rolling median and median absolute deviation (MAD), with a
rolling standard-deviation fallback when MAD is effectively zero. The default
minimum history equals the full lookback window; insufficient baselines are
explicitly marked and do not produce anomaly flags.

## Interpretation

The bounded 0-100 event score is a relative anomaly-severity score, not a
probability, confidence estimate, expected return or directional forecast.
Individual anomaly types and robust z-scores are returned as evidence. A
volume spike does not establish informed trading; a price-volume dislocation
does not establish spoofing or manipulation.

## Research controls

- Every feature at bar t uses observations through t only.
- Future bars cannot alter prior anomaly scores.
- Invalid OHLCV, duplicate/non-monotonic timestamps and impossible price geometry
  are rejected rather than silently repaired.
- No gap filling, forward filling, interpolation or synthetic observations.
- Thresholds are transparent configuration parameters and require chronological
  out-of-sample evaluation before production interpretation.

## Safety

The output is research-only and unconditionally non-actionable. It does not
create trade plans, update strategy weights, or submit orders.
