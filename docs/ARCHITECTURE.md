# Architecture

Bounded contexts: Identity, Market Data, Quant, Backtest, Portfolio/Risk, Research, AI, UI.

Dependency direction: API -> application -> domain. Infrastructure implements interfaces. Domain must not depend on web frameworks, databases, exchange SDKs, or LLM libraries.

MVP: local secure app shell, market-data ingestion, charting, deterministic single-strategy backtest. No live trading.


## Implemented Market Data Domain
- Canonical OHLCV candle model uses timezone-aware timestamps normalized to UTC and Decimal price/volume fields.
- Candle series quality validation rejects empty and non-increasing timestamp sequences.
- Provider adapters, gap detection, persistence, and API routes remain planned.


### Candle gap detection
Fixed-interval missing timestamps are detectable and returned without auto-filling synthetic bars. Provider-specific interval metadata and gap policy are still required before ingestion can be enabled.


## Market data provider boundary
- `BinanceSpotAdapter` implements a public Spot Kline provider with bounded limit and interval validation, time-aware optional start/end filters, Decimal conversion, UTC timestamps, and provider error normalization.
- `GET /api/v1/market-data/candles` requires the local authenticated session and provides sanitized provider errors.
- The provider is tested using HTTP mock transport; CI must not depend on live exchange availability.
- Next work: provider-neutral interface, exchange metadata/symbol canonicalization, rate-limit/backoff, caching, incomplete-bar handling, pagination, and live stream.
