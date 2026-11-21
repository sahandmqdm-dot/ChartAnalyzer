# Architecture

Bounded contexts: Identity, Market Data, Quant, Backtest, Portfolio/Risk, Research, AI, UI.

Dependency direction: API -> application -> domain. Infrastructure implements interfaces. Domain must not depend on web frameworks, databases, exchange SDKs, or LLM libraries.

MVP: local secure app shell, market-data ingestion, charting, deterministic single-strategy backtest. No live trading.


## Implemented Market Data Domain
- Canonical OHLCV candle model uses timezone-aware timestamps normalized to UTC and Decimal price/volume fields.
- Candle series quality validation rejects empty and non-increasing timestamp sequences.
- Provider-neutral `MarketDataProvider` protocol defines the normalized OHLCV contract.
- `Symbol` normalizes case and whitespace and rejects unsupported characters before provider calls.
- `Timeframe` defines canonical intervals independently of an exchange; fixed intervals expose seconds, while calendar month intervals deliberately have no fixed duration.
- `BinanceSpotAdapter` is the first implementation; the registry supplies it as the default provider.
- API routes receive providers through FastAPI dependency injection, so tests and future adapters can replace the implementation without changing route logic.


### Candle gap detection
Fixed-interval missing timestamps are detectable and returned without auto-filling synthetic bars. Provider-specific interval metadata and gap policy are still required before ingestion can be enabled.


## Market data provider boundary
- `BinanceSpotAdapter` implements a public Spot Kline provider with bounded limit and interval validation, time-aware optional start/end filters, Decimal conversion, UTC timestamps, and provider error normalization.
- Binance requests use bounded retries for transport failures, HTTP 429, and selected transient 5xx responses. `Retry-After` is honored when numeric; otherwise capped exponential backoff is used. Client errors are not retried, and retry sleep is injectable for deterministic tests.
- `GET /api/v1/market-data/candles` requires the local authenticated session and provides sanitized provider errors.
- The provider is tested using HTTP mock transport; CI must not depend on live exchange availability.
- Provider selection is centralized in `market_data.registry`; Binance Spot is currently the only configured provider.


## Short-lived market data cache
The candles API uses a process-local asynchronous TTL/LRU cache with a 15-second lifetime and a 512-key bound. Cache keys include normalized symbol, interval, limit, and optional time bounds. The cache is intentionally non-durable and per-process; shared Redis caching and request coalescing remain future work.

## Remaining market data work
Incomplete-candle policy, historical pagination, persistent storage, and live streaming are not implemented yet. Retry/backoff is implemented but still needs operational tuning against exchange behavior.
