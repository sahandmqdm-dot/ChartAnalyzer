# Execution Cost Engine

## Scope and safety boundary

execution_cost_engine.py is a research accounting component. It does not send
orders, tune signals, predict fills, or authorize deployment. It accounts for
supplied observed or explicitly simulated fills. It is separate from the
OHLCV backtest engine until a validated event-driven replay adapter exists.

## Required inputs

- Venue, market, account tier, maker/taker rates, source and effective time.
- Reference mid and contemporaneous best bid/ask.
- Requested quantity and zero or more fills, each with price, quantity and
  explicit maker or taker liquidity role.
- Fill provenance for any supplied fills.
- Explicit impact basis points and, when funding events apply, a funding rate.
  Missing impact or applicable funding inputs are flagged and make the cost
  breakdown incomplete, rather than silently asserting zero cost.
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
- Funding is applied only from explicit funding inputs. Latency is retained as
  context and is not converted into fabricated slippage.
- Every report carries fee schedule provenance, a schedule hash, fill source,
  assumptions and an assumptions hash.

## Integration boundary

The existing bar backtest remains a bar-level next-open simulator with
configured fee/spread/slippage/impact inputs. This engine does not claim to
replace it or reconstruct historical order-book execution. A future adapter
must consume verified point-in-time book/trade replay, model queue position and
partial fills, and pass independent tests before any result is promoted.

## Tests

tests/test_execution_cost_engine.py covers maker/taker accounting,
partial/unfilled orders, adverse slippage, funding, provenance and fail-closed
validation. All outputs are research-only and do not submit live orders.
