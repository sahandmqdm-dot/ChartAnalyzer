# Architecture

Bounded contexts: Identity, Market Data, Quant, Backtest, Portfolio/Risk, Research, AI, UI.

Dependency direction: API -> application -> domain. Infrastructure implements interfaces. Domain must not depend on web frameworks, databases, exchange SDKs, or LLM libraries.

MVP: local secure app shell, market-data ingestion, charting, deterministic single-strategy backtest. No live trading.


## Implemented Market Data Domain
- Canonical OHLCV candle model uses timezone-aware open/close timestamps normalized to UTC, Decimal price/volume fields, and an explicit `is_closed` completion flag.
- Candle series quality validation rejects empty and non-increasing timestamp sequences.
- Provider-neutral `MarketDataProvider` protocol defines the normalized OHLCV contract.
- `Symbol` normalizes case and whitespace and rejects unsupported characters before provider calls.
- `Timeframe` defines canonical intervals independently of an exchange; fixed intervals expose seconds, while calendar month intervals deliberately have no fixed duration.
- `BinanceSpotAdapter` and `BybitSpotAdapter` implement the same normalized Spot OHLCV contract; `MARKET_DATA_PROVIDER` selects the configured adapter.
- API routes receive providers through FastAPI dependency injection, so tests and future adapters can replace the implementation without changing route logic.


### Candle gap detection
Fixed-interval missing timestamps are detectable and returned without auto-filling synthetic bars. Provider-specific interval metadata and gap policy are still required before ingestion can be enabled.


## Market data provider boundary
- `BinanceSpotAdapter` implements a public Spot Kline provider with bounded limit and interval validation, time-aware optional start/end filters, Decimal conversion, UTC timestamps, provider error normalization, and open/closed candle classification based on Binance Kline close time.
- `fetch_historical_ohlcv` retrieves bounded historical ranges page-by-page, advances using the previous candle close time plus one millisecond, filters duplicate/old boundary rows, and fails closed if pagination stops advancing.
- Binance requests use bounded retries for transport failures, HTTP 429, and selected transient 5xx responses. `Retry-After` is honored when numeric; otherwise capped exponential backoff is used. Client errors are not retried, and retry sleep is injectable for deterministic tests.
- `GET /api/v1/market-data/candles` and `GET /api/v1/market-data/candles/history` require the local authenticated session and provide sanitized provider errors. The history endpoint requires timezone-aware start/end bounds and caps page size at 1000 and total candles at 10000.
- The provider is tested using HTTP mock transport; CI must not depend on live exchange availability.
- Provider selection is centralized in `market_data.registry`; Binance Spot and Bybit Spot are supported. The setting defaults to `binance_spot`, and invalid provider names fail configuration validation. Bybit Spot does not support the 3-day or 8-hour intervals.


## Short-lived market data cache
The candles API uses a process-local asynchronous TTL/LRU cache with a 15-second lifetime and a 512-key bound. Cache keys include provider, normalized symbol, interval, limit, and optional time bounds. Concurrent misses are coalesced with single-flight loading. The cache is intentionally non-durable and per-process; shared Redis caching remains future work.

## Remaining market data work
Bounded historical pagination is implemented in the Binance adapter; persistent storage and live streaming are not implemented yet. Candle completion is exposed as metadata; downstream backtests must explicitly exclude open candles. Retry/backoff is implemented but still needs operational tuning against exchange behavior.


### Closed-candle consumption

Both market-data candle endpoints accept `closed_only=true` to exclude the currently forming candle from the response. The default remains `false` for backward compatibility; backtests and any analysis requiring finalized OHLCV data must opt in to `closed_only=true`. The cache stores the unfiltered provider result, and filtering is applied to each response so the flag does not create duplicate cache entries.


### Market-data cache concurrency and provider isolation

The process-local market-data cache coalesces concurrent misses for the same key: only one provider request runs, and concurrent callers await the same shielded task. Cancelling one HTTP request does not cancel work shared by other callers. Failed loads are not cached and can be retried. Cache keys include the provider name so results from different providers cannot collide. This is an in-process optimization; separate worker processes maintain separate caches.
