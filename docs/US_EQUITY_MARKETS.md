# US Equity Calendar and Venue Rules

## Scope
The baseline calendar and rule validator cover Nasdaq/NYSE cash equities. They do not connect to a broker, route orders, discover live halts, retrieve NBBO, locate borrow or certify exchange compliance.

## Calendar
- Uses IANA `America/New_York` timezone, so DST is applied by the timezone database.
- Classifies pre-market (04:00-09:30 ET), regular session (09:30-16:00 ET), and after-hours (regular close-20:00 ET).
- Applies a deterministic baseline holiday set and common 13:00 ET early closes.
- Early-close and holiday lists are calendar rules, not live exchange notices. Exceptional closures and schedule changes must be supplied by a trusted calendar provider before production.
- The calendar is not yet wired into `/api/analyze` or the market-data normalizer. It must not be used as proof that a symbol is not halted.

## Equity rules
- Requires normalized symbol, venue, trading status, tick size, lot size, quantity limits, minimum notional and shortable status.
- Rounds share quantity down to lot size; rounds long stops up/targets down and short stops down/targets up.
- Blocks short plans unless borrow availability is explicitly confirmed; if SSR is active, explicit short-sale price-test compliance is required.
- Output is always non-actionable and does not submit orders.

## Production integration gates
A production adapter must provide timestamped and source-attributed listing metadata, live trading status, venue/broker-specific price increments, corporate-action adjusted symbol identity, borrow locate and (where relevant) NBBO/SSR compliance. Missing or stale metadata must produce NO_TRADE.
