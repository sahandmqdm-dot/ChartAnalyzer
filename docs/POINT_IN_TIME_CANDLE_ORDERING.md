# Point-in-Time Candle Ordering Contract

## Scope
This change closes one specific integrity gap: a calculation can be mathematically
causal by row position yet still use future observations if the input rows are
unordered, duplicated, or reversed in time.

## Enforced behavior
- `trend_engine.series` now requires a `time` column with finite, unique,
  strictly increasing timestamps.
- `market_regime_intelligence.analyze_market_regimes` validates timestamps
  whenever the input includes a `time` column.
- `predictive_intelligence.analyze` validates timestamps whenever present and
  reports `input_order_status`:
  - `VERIFIED_STRICTLY_INCREASING`
  - `UNVERIFIED_NO_TIMESTAMP`

The regime and PMI functions retain support for timestamp-free inputs for
backward compatibility. Such inputs are explicitly unverified and callers must
supply rows in chronological order. Timestamp-free output must not be described
as independently verified point-in-time data.

## Existing causal controls retained
- Market regime features use trailing rolling windows and current-bar values.
- Market anomaly baselines are shifted by one bar, so the current observation
  is compared with strictly prior observations.
- PMI momentum, breakout references, and bootstrap pool use only supplied
  history; probabilities remain withheld pending calibration.
- Trend structure uses confirmed pivots, and existing prefix-invariance tests
  cover the trend score and structural state.
- App data loading removes forming candles for supported live feeds.

## What this does not prove
Monotonic timestamps do not prove that the source data was available at the
decision time, immutable, unrevised, or free from vendor backfill. Nor do they
prove correct timezone semantics or exchange-session handling. Stronger
point-in-time provenance requires raw-source snapshots, ingestion timestamps,
revision tracking, and reproducible data fingerprints. This change is a
necessary input-order guard, not a complete PIT data-vault implementation.
