# Effective Sample Size (ESS) Research Diagnostic

## Status
This module is isolated and is deliberately NOT connected to the production
deployment gate. No ESS floor is asserted. A minimum ESS must be justified by
power simulations using the actual return distribution, dependence, frequency,
and decision threshold.

## Two distinct estimands
1. Trade-level ESS uses chronological completed non-overlapping trade net_ret
   values. In backtest_engine.run_backtest, net_ret is the compounded net
   account return for that trade after modeled fees, spread, slippage, impact,
   and funding. It is not raw price return.
2. Time-series ESS uses one net portfolio equity return for every consecutive
   evaluation period, including flat periods. It must not use a trade-only
   series. timestamped_portfolio_ess verifies regular timestamps and rejects
   missing or embargo periods instead of compressing them.

## Estimator
Newey-West HAC with Bartlett kernel:
Omega = gamma(0) + 2 * sum(w_k * gamma(k))
w_k = 1 - k / (L + 1)
ESS = clip(N * gamma(0) / Omega, 1, N)

Default bandwidth is floor(4 * (N / 100) ** (2 / 9)), bounded to [1,N-1].
An optional holding-period bandwidth can increase it. Sensitivity across
bandwidths must be examined before inferential use.

## Current pipeline limitations
- walk_forward.run_walk_forward appends each fold equity curve into one list
  without timestamps and without explicit rows for embargo gaps.
- Each fold starts flat and is liquidated at its boundary; positions do not
  carry between folds.
- The concatenated curve is not a continuous portfolio return series.
- The frozen signal is passed as a precomputed full-length array. The
  walk-forward function documents causality but cannot prove upstream
  point-in-time feature construction.
- research_statistics moving-block bootstrap callers determine whether their
  input is trade-level or bar-level; this semantic must be audited per call.

## Safe use
Use trade_level_effective_sample_size(result["trades_log"]) for the trade
diagnostic. Do not connect it to deployment eligibility yet. Time-series ESS
remains unavailable until the engine emits timestamped period-aligned equity
returns including flat periods and an auditable mask for excluded embargo bars.

The existing 200 raw OOS trades policy threshold is not an ESS threshold.
Before promotion, test IID, positive and negative autocorrelation, short,
constant, missing, irregular-time, and overlapping-trade cases, and run power
simulations. ESS alone is not proof of an edge.
