# Funding Rate & Open Interest Intelligence

## Scope

`funding_open_interest.py` introduces provider-neutral, read-only contracts for
perpetual-futures funding observations and open-interest observations.

- Funding rates are represented as decimal fractions per stated funding interval.
- Open interest carries an explicit unit and optional quote notional.
- Every observation carries a timezone-aware timestamp, provider and canonical instrument ID.
- Freshness is evaluated independently for funding and open interest.
- OI percentage change is calculated only against a prior observation with the same
  instrument, provider and unit, outside the configured comparison window.
- Missing history is reported as unavailable; values are never fabricated or interpolated.

## Safety

The analyzer returns `actionable: false` unconditionally. Stale observations make
the context invalid and return `NO_TRADE_STALE_DERIVATIVES_DATA`. A positive OI
change does not imply new longs, and a negative change does not imply new shorts.
Funding and OI are context features, not standalone trade signals.

## Provider adapter contract

Adapters should normalize upstream payloads into:

- `FundingObservation`: instrument ID, provider, observed timestamp, decimal
  funding rate, funding interval hours, and optional next-funding timestamp,
  mark price and index price.
- `OpenInterestObservation`: instrument ID, provider, observed timestamp,
  open-interest value, explicit unit, and optional quote-currency notional.

Adapters must preserve source timestamps and units, reject unknown symbols,
avoid silently mixing linear/inverse contracts, and expose provider outages and
rate limits. Do not treat a locally received timestamp as the upstream event
timestamp unless the provider supplies no event timestamp and the adapter
explicitly marks that limitation.

## Deliberate non-goals

No exchange-specific API calls, credentials, order placement, funding payment
forecast, liquidation oracle, automatic position inference, strategy-weight
changes or backtest integration are included in this foundation PR. Those
require separately validated adapters and integration tests.
