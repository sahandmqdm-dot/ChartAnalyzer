# Multi-Asset Architecture Roadmap

## Scope and status
ChartAnalyzer is being extended from a crypto-oriented research application toward a provider-neutral platform for Crypto Spot/Futures and US-listed equities, initially Nasdaq and NYSE. The first implementation slice is a shared domain package: canonical instrument identity, a conflict-safe registry and normalized OHLCV candle contracts.

This foundation is not yet wired into the legacy /api/analyze path. No exchange or broker adapter is enabled, no market-data entitlement is assumed, and no order submission is implemented. Normalized market data is not a trading authorization.

## Target data flow
Provider Adapters -> Market Data Hub -> Canonical Instrument Registry -> Data Normalization and Integrity -> Feature and Technical Engines -> Market Regime and Structure Intelligence -> Signal Diagnostics -> Chronological OOS and Robustness Validation -> Deployment Authorization -> Asset-Specific Risk Engines -> Venue Constraints -> Non-Actionable Trade Plan -> Paper Trading and Simulation.

Every stage must preserve source, venue, symbol, interval, timezone, retrieval time and dataset/config fingerprints. Missing or stale metadata blocks downstream eligibility.

## Instrument model
Instrument separates stable internal canonical_id from venue-native symbol. Each instrument carries asset class, market type, quote/base currency, venue timezone, status, contract multiplier and explicit linear/inverse type for futures. US equities use America/New_York and crypto timestamps normalize to UTC. DST handling belongs to timezone-aware calendars, never fixed UTC offsets.

## Market data contracts
MarketDataNormalizer accepts provider mappings, normalizes epoch seconds/milliseconds and timezone-aware ISO timestamps to UTC, validates finite positive OHLC, non-negative volume and OHLC relationships, sorts chronologically, rejects duplicate timestamps and reports missing intervals. It never fabricates candles or fills gaps.

The current gap detector is fixed-duration only. Equity holidays, early closes, halts, pre-market and after-hours require a future exchange-calendar/session adapter; overnight closures must not be mistaken for missing candles.

## Asset-specific engines
Crypto: separate Spot and linear/inverse Futures identities; exchange metadata, tick/lot/notional filters, funding, open interest, mark price, liquidation and order-book depth require independent timestamped contracts and venue adapters.

US equities: Nasdaq-listed common stocks and ETFs first, with NYSE via a separate venue adapter. Required extensions include regular/pre-market/after-hours sessions, holidays, early closes, halts, splits, dividends, symbol changes, delistings, point-in-time SEC fundamentals, short-sale eligibility and borrow constraints.

## Risk and validation
Shared portfolio risk may aggregate exposures, currency conversion and correlations, but each asset class retains its own market, margin and execution rules. Futures sizing consumes validated exchange filters and tiered margin metadata. Equity sizing respects tick increments, session status and short-sale rules. OOS authorization binds to exact instrument universe, interval, strategy fingerprint, data fingerprint, execution costs, software version and expiry. AI cannot bypass data-integrity, validation, risk or venue gates.

## Delivery sequence
1. Multi-asset domain contracts (this change).
2. Session-aware US market calendar and equity venue rules.
3. Market Data Hub interfaces and read-only provider adapters.
4. Integrate futures filters and tiered margin/liquidation using exchange metadata.
5. Crypto funding, open-interest and order-book contracts.
6. Nasdaq/NYSE data adapter and point-in-time fundamentals.
7. Shared feature and regime layer with asset-specific features.
8. Portfolio exposure, correlation and allocation.
9. Paper trading, event replay and cost/slippage simulation.
10. Cloud deployment with secret isolation, observability, quotas and persistent experiment lineage.

## Provider acceptance criteria
Explicit provider, venue, instrument, currency, timezone, interval and retrieval timestamp; no silent symbol remapping or synthetic candle filling; bounded retry/backoff and rate limits; deterministic schema fixtures; read-only credentials; data licensing and realtime entitlements verified before production.
