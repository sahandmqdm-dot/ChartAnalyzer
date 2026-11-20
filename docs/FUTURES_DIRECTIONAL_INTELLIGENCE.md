# Futures Directional Intelligence v1

## Scope

The module `futures_signal_engine.py` creates transparent LONG and SHORT
watch scenarios from completed, chronological OHLCV candles. It is a
research-facing candidate layer, not an order signal or a deployment gate.

## Output semantics

- `candidate`: LONG, SHORT, or NO_DIRECTIONAL_SETUP from a bounded heuristic.
- `scenarios.long` and `scenarios.short`: counterfactual reference entry,
  ATR stop and R-multiple target for both directions.
- `WATCH_ONLY`: scenario visualization only. `actionable` is always false.
- `decision`: always NO_TRADE until a separate futures-specific OOS workflow,
  execution model and deployment authorization exist.
- `directional_score`: heuristic score, not a probability or confidence claim.

## Inputs and integrity

At least 60 bars are required. Each bar must include OHLCV and a timezone-aware
close timestamp (`close_time` or `time`). Timestamps must be strictly
increasing, gap-free within the configured tolerance, not future-dated, and
fresh relative to `as_of`. The caller must supply completed candles only.
No sorting, deduplication, interpolation or synthetic timestamps are performed.

## Features

EMA20/EMA50 relationship, six-bar price momentum, RSI14 and ATR14 form a
transparent bounded score. Funding and open-interest change are freshness-
checked context only. OI change is never interpreted as long/short positioning.
Funding is not used as a directional vote.

## Important limitations

The initial engine does not yet consume L2 order-book imbalance, tick trades,
CVD, liquidation streams, mark/index premium, funding accrual over the holding
horizon, contract-specific maintenance tiers, or executable bid/ask prices.
It does not claim calibrated probabilities, positive expectancy, OOS validity,
or profitability. Stop/target references do not model intrabar ordering.
