# Architecture

Bounded contexts: Identity, Market Data, Quant, Backtest, Portfolio/Risk, Research, AI, UI.

Dependency direction: API -> application -> domain. Infrastructure implements interfaces. Domain must not depend on web frameworks, databases, exchange SDKs, or LLM libraries.

MVP target: local research shell, market-data ingestion, charting, and deterministic single-strategy backtesting. Current branch implements the local shell, basic close-price chart, and a deterministic long-only SMA crossover backtest with next-bar-open execution, candle-close mark-to-market equity, drawdown, trade-quality metrics, and an authenticated API endpoint. Persistent storage and live streaming are not implemented. No live trading.


## Implemented Market Data Domain
- Canonical OHLCV candle model uses timezone-aware open/close timestamps normalized to UTC, Decimal price/volume fields, and an explicit `is_closed` completion flag.
- Candle series quality validation rejects empty and non-increasing timestamp sequences.
- Provider-neutral `MarketDataProvider` protocol defines the normalized OHLCV contract.
- `Symbol` normalizes case and whitespace and rejects unsupported characters before provider calls.
- `Timeframe` defines canonical intervals independently of an exchange; fixed intervals expose seconds, while calendar month intervals deliberately have no fixed duration.
- `BinanceSpotAdapter` and `BybitSpotAdapter` implement the same normalized Spot OHLCV contract; `MARKET_DATA_PROVIDER` selects the configured adapter.
- API routes receive providers through FastAPI dependency injection, so tests and future adapters can replace the implementation without changing route logic.


### Candle gap detection
Fixed-interval missing timestamps can be detected without auto-filling synthetic bars. Gap detection is diagnostic only; no synthetic candles are generated.


## Market data provider boundary
- `BinanceSpotAdapter` and `BybitSpotAdapter` implement public Spot Kline providers with bounded limits, exchange-specific interval validation, time-aware optional start/end filters, Decimal conversion, UTC timestamps, normalized provider errors, and open/closed candle classification. Bybit returns reverse-ordered rows; its adapter sorts them chronologically and derives candle close time from the selected interval.
- Both adapters implement bounded historical pagination. Binance advances forward using the previous candle close time plus one millisecond; Bybit pages backward from the requested end because its Kline API returns the newest rows first. Both reject pagination that does not make progress and return ascending, validated candle series.
- Binance requests use bounded retries for transport failures, HTTP 429, and selected transient 5xx responses. `Retry-After` is honored when numeric; otherwise capped exponential backoff is used. Client errors are not retried, and retry sleep is injectable for deterministic tests.
- `GET /api/v1/market-data/candles` and `GET /api/v1/market-data/candles/history` require the local authenticated session and provide sanitized provider errors. The history endpoint requires timezone-aware start/end bounds and caps page size at 1000 and total candles at 10000.
- The provider is tested using HTTP mock transport; CI must not depend on live exchange availability.
- Provider selection is centralized in `market_data.registry`; Binance Spot and Bybit Spot are supported. The setting defaults to `binance_spot`, and invalid provider names fail configuration validation. Bybit Spot does not support the 3-day or 8-hour intervals.


## Short-lived market data cache
The candles API uses a process-local asynchronous TTL/LRU cache with a 15-second lifetime and a 512-key bound. Cache keys include provider, normalized symbol, interval, limit, and optional time bounds. Concurrent misses are coalesced with single-flight loading. The cache is intentionally non-durable and per-process; shared Redis caching remains future work.

## Remaining market data work
Bounded historical pagination is implemented in both exchange adapters; persistent storage and live streaming are not implemented yet. Candle completion is exposed as metadata; downstream backtests must explicitly exclude open candles. Retry/backoff is implemented but still needs operational tuning against exchange behavior.


### Closed-candle consumption

Both market-data candle endpoints accept `closed_only=true` to exclude the currently forming candle from the response. The default remains `false` for backward compatibility; backtests and any analysis requiring finalized OHLCV data must opt in to `closed_only=true`. The cache stores the unfiltered provider result, and filtering is applied to each response so the flag does not create duplicate cache entries.


### Market-data cache concurrency and provider isolation

The process-local market-data cache coalesces concurrent misses for the same key: only one provider request runs, and concurrent callers await the same shielded task. Cancelling one HTTP request does not cancel work shared by other callers. Failed loads are not cached and can be retried. Cache keys include the provider name so results from different providers cannot collide. This is an in-process optimization; separate worker processes maintain separate caches.


## Derivatives market-data boundary
- The derivatives overview and history APIs are read-only and require the existing authenticated session. No exchange API credentials or trading permissions are used.
- `BinanceUsdmFuturesHistoryAdapter` normalizes USDⓈ-M funding history, open-interest statistics, and global long/short account ratio. `BybitLinearFuturesHistoryAdapter` normalizes the equivalent V5 linear endpoints. Both return Decimal values and UTC timestamps in chronological order.
- `GET /api/v1/derivatives/history` accepts a normalized symbol, one common interval, and a bounded record count. A process-local 60-second TTL/LRU cache coalesces repeated requests. The three upstream series are requested concurrently; provider errors are sanitized into HTTP 502 responses.
- Source semantics are not identical: Binance long/short ratio is the global account ratio; Bybit ratio is calculated as buyRatio divided by sellRatio. The secondary ratio field preserves the provider's long/buy share where available. Open-interest units and notional fields remain exchange-defined and should not be compared across contracts without contract metadata.
- Exchange retention differs. Binance open-interest history exposes only the latest month and its global long/short account ratio the latest 30 days. This endpoint does not yet paginate beyond the bounded recent window or persist series to a database.
- The frontend plots the three normalized series separately. These are descriptive market-context indicators, not standalone trading signals. Liquidation analytics and futures-aware backtesting (including funding, leverage, margin, and liquidation mechanics) are not implemented.


## Futures intelligence interpretation
- `GET /api/v1/derivatives/analysis` combines the latest two closed candles from the configured Spot provider with normalized derivatives history from the configured futures venue. The route is authenticated and read-only.
- The deterministic analyzer describes price/OI combinations (price up + OI up, price down + OI up, and the two falling-OI cases), a simple funding threshold, and account-ratio context. It emits individual explanations, a qualitative confidence label, and explicit caveats.
- Price return is a Spot proxy because historical futures mark-price candles are not yet wired into the common candle model. Funding thresholds are exploratory constants, not volatility-normalized or backtested parameters.
- “Squeeze conditions” labels are heuristic conjunctions (e.g. rising OI, falling price, and strongly positive funding for long-squeeze conditions). They do not detect liquidations, identify who is trapped, or forecast a squeeze. Long/short account ratios are account counts/ratios, not position-size-weighted exposure.
- The endpoint does not emit buy/sell orders or exchange actions. Treat output as a market-context annotation and not as a recommendation.
