# Effective Sample Size (ESS) Research Diagnostic

## Status
This module is isolated from the production deployment gate. No ESS floor is
asserted. A minimum ESS must be justified by power simulations using the actual
return distribution, dependence, frequency, and decision threshold.

## Two distinct estimands
1. **Trade-level ESS** uses chronological completed non-overlapping trade net_ret
   values. In backtest_engine.run_backtest, net_ret is the compounded net
   account return for that trade after modeled fees, spread, slippage, impact,
   and funding. It is not raw price return.
2. **Time-series ESS** uses one net portfolio equity return for every consecutive
   evaluation period, including flat periods. It must not use a trade-only series.

## Timestamped equity contract
run_backtest now emits:
- equity_curve: starting equity at the first executable fill open, followed by
  mark-to-market equity at each subsequent open;
- equity_bar_indices: exact source candle row index for every equity point;
- equity_timestamps: source-native time values when a valid time column or
  DatetimeIndex exists;
- equity_timestamp_source: the source field used, or null when no time axis
  was provided.

Numeric timestamps retain their original unit. The engine does not guess
seconds versus milliseconds. Timestamp values must be finite, unique, and
strictly increasing. A candle frame without a real time axis remains usable for
bar-indexed backtests, but is not eligible for time-series ESS.

## Walk-forward segments and embargo
run_walk_forward now emits equity_timeline rows with fold id, segment id,
global bar index, timestamp, scaled equity, and an explicit false flag for
cross-fold ESS inclusion. It also emits equity_timeline_metadata with
excluded embargo intervals.

Each fold starts with a baseline equity mark at its first executable OOS open,
then emits its subsequent marks. Per-fold time-series ESS is computed only when
a real timestamp axis exists and is regular. Missing/irregular timestamps make
that fold's ESS unavailable.

A cross-fold ESS is deliberately not computed. Fold resets and excluded embargo
periods mean the concatenated observations are not one uninterrupted return
series. A future aggregation method must handle independent segments and
between-fold dependence explicitly; it must never compress gaps.

## Estimator
Newey-West HAC with Bartlett kernel:
Omega = gamma(0) + 2 * sum(w_k * gamma(k))
w_k = 1 - k / (L + 1)
ESS = clip(N * gamma(0) / Omega, 1, N)

Default bandwidth is floor(4 * (N / 100) ** (2 / 9)), bounded to [1,N-1].
An optional holding-period bandwidth can increase it. Sensitivity across
bandwidths must be examined before inferential use.

## Remaining limitations
- The walk-forward signal is supplied as a precomputed full-length array.
  Walk-forward can enforce chronology and fold boundaries, but cannot prove
  upstream point-in-time feature construction.
- Per-fold ESS is diagnostic only; it is not a gate threshold or proof of edge.
- The existing raw OOS trade-count threshold is not an ESS threshold.
- ESS does not establish statistical power. Power simulation and sensitivity
  analysis are required before it can influence deployment eligibility.

## Safe use
Use trade_level_effective_sample_size(result["trades_log"]) for the
trade-level diagnostic. Use the timestamped fold output for per-fold time-series
ESS. Do not calculate one ESS from concatenated folds. Before promotion, test
IID, positive and negative autocorrelation, short, constant, missing,
irregular-time, and overlapping-trade cases.
