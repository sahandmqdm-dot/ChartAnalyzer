# Execution Cost Engine

## Scope and safety boundary

execution_cost_engine.py is a research accounting component. It does not send
orders, tune signals, predict fills, or authorize deployment. It accounts for
supplied observed or explicitly simulated fills. It is separate from the
OHLCV backtest engine. A point-in-time L2 replay adapter is available for sealed capture sessions, but it does not yet replace the bar simulator or model queue-aware fills.

## Required inputs

- Venue, market, account tier, maker/taker rates, source and effective time.
- Reference mid and contemporaneous best bid/ask.
- Requested quantity and zero or more fills, each with price, quantity and
  explicit maker or taker liquidity role.
- Fill provenance for any supplied fills.
- Explicit impact basis points and traceable impact-model source; when funding events apply, a funding rate and funding-data source.
  Missing impact values, impact provenance or applicable funding inputs/provenance are flagged and make the cost breakdown incomplete, rather than silently asserting zero cost.
- Optional fill probability is accepted only with a calibration flag and a
  traceable model source. It is reported as a supplied estimate, not used to
  fabricate fills or expected PnL.

## Accounting semantics

- Fees are calculated per fill using the fill's liquidity role. A limit order
  is not automatically maker.
- A zero-fill limit order remains unfilled; venue rejection is represented
  separately; a partial fill is charged only on executed quantity. No
  price-touch heuristic creates a fill.
- Spread cost is measured from reference mid to contemporaneous touch.
  Slippage is adverse execution beyond that touch. Impact is a separately
  supplied assumption because OHLCV alone does not reveal impact.
- Funding is applied only from explicit funding inputs. The supplied funding rate must be signed from the held-position perspective (positive means a cost, negative a credit); the engine does not infer position-side funding. Latency is retained as context and is not converted into fabricated slippage.
- Every report carries fee schedule provenance, a schedule hash, fill source,
  assumptions and an assumptions hash. The registry adapter persists this
  breakdown with the experiment and marks incomplete cost models separately
  from completed evidence; missing cost inputs cannot silently become a
  completed cost experiment.

## Integration boundary

The existing bar backtest remains a bar-level next-open simulator with
configured fee/spread/slippage/impact inputs. `replay_historical_market_orders`
now replays hypothetical market orders against sealed, hash-verified L2 capture
sessions. It uses only book events whose local `received_at` is no later than
the decision timestamp, rejects incomplete or sequence-invalid sessions, and
blocks decisions with stale or unavailable books. It estimates displayed depth
and partial fills; it is not a realized execution replay. It does not model
queue position, hidden liquidity, cancellations or intervening matching events.
The adapter does not yet use tick trades to generate strategy decisions or
validate signal forecasts. Limit-order fill probability remains an externally
supplied calibrated estimate; no limit fill is generated from price touch or
probability alone.

## Tests

tests/test_execution_cost_engine.py covers maker/taker accounting,
partial/unfilled/rejected orders, adverse slippage, funding, calibrated fill
probability provenance, stale-book rejection, L2 depth sweeps and fail-closed
validation. All outputs are research-only and do not submit live orders.
