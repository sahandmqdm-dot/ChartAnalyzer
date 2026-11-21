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
- `GET /api/v1/derivatives/analysis` combines the latest two closed futures Mark Price candles with normalized derivatives history from the configured futures venue. The route is authenticated and read-only.
- The deterministic analyzer describes Mark Price/OI combinations (price up + OI up, price down + OI up, and the two falling-OI cases), a simple funding threshold, and account-ratio context. It emits individual explanations, a qualitative confidence label, and explicit caveats.
- Price return is calculated from the exchange's futures Mark Price candles. Mark Price is the reference used in margin calculations and can differ from the latest traded price. Funding thresholds are exploratory constants, not volatility-normalized or backtested parameters.
- “Squeeze conditions” labels are heuristic conjunctions (e.g. rising OI, falling price, and strongly positive funding for long-squeeze conditions). They do not detect liquidations, identify who is trapped, or forecast a squeeze. Long/short account ratios are account counts/ratios, not position-size-weighted exposure.
- The endpoint does not emit buy/sell orders or exchange actions. Treat output as a market-context annotation and not as a recommendation.


## Futures mark-price history
- `GET /api/v1/derivatives/mark-price-history` returns bounded linear-perpetual mark-price OHLC candles from Binance USDⓈ-M (`/fapi/v1/markPriceKlines`) or Bybit V5 (`/v5/market/mark-price-kline`). It is authenticated and read-only.
- The adapters normalize exchange tuples into UTC timestamps, Decimal OHLC values, deterministic chronological order, and an explicit candle-closed flag. The API accepts `5m`, `15m`, `30m`, `1h`, `4h`, and `1d`, with a maximum of 200 rows and a 30-second process-local cache.
- Mark-price candles contain no meaningful traded volume in these endpoints; the domain intentionally models only OHLC and timestamps rather than inventing volume.
- The futures intelligence analyzer now uses Mark Price candles for its price/OI interpretation. This endpoint alone does not estimate liquidation prices or identify liquidation clusters.


## Isolated futures risk estimate
- `POST /api/v1/derivatives/risk-estimate` accepts a linear contract side, entry price, base-asset quantity, leverage, optional extra isolated margin, estimated close-fee rate, and an explicit ordered risk-tier table. It solves the liquidation-price equation against each tier and selects the tier containing the resulting liquidation notional.
- Tier records carry notional floor/cap, maintenance-margin rate, cumulative maintenance deduction, and optional maximum leverage. Finite final caps are preserved; notionals outside the supplied table are rejected rather than extrapolated.
- `GET /api/v1/derivatives/risk-tiers?symbol=BTCUSDT` retrieves public Bybit V5 linear risk-limit tiers and caches them for five minutes. Binance `/fapi/v1/leverageBracket` is a signed USER_DATA endpoint, so this unauthenticated market-data service deliberately does not claim to fetch Binance account-specific brackets. Verified Binance tiers can be supplied to the calculator by the caller.
- The calculator is strictly isolated-margin and estimate-only. It does not model cross/portfolio margin, multi-position collateral, funding cashflows, open orders, ADL, or the full exchange liquidation engine. The result is not a guaranteed liquidation trigger price.


## Live liquidation event collector
- The authenticated on-demand stream controls connect to Binance USDⓈ-M `{symbol}@forceOrder` or Bybit V5 linear `allLiquidation.{symbol}`. Exchange payloads are normalized into a common event with exchange, symbol, event time, liquidated side, order side, quantity, price, quote notional, and price basis.
- Binance force-order `SELL` closes a long and `BUY` closes a short; its average execution price and last filled quantity are preferred over the order's limit price/original quantity. Bybit documents `Buy` as a liquidated long and `Sell` as a liquidated short; its reported price is bankruptcy price, not average execution price.
- The collector is on-demand, reconnects after transport failures, deduplicates event IDs, and retains at most 2,000 events per symbol in process memory. The frontend polls the authenticated snapshot endpoint while the stream is active. Shutdown cancels active collectors.
- The exchange streams are snapshots, not a complete historical liquidation tape: Binance emits at most the latest liquidation order per symbol per 1-second interval; Bybit pushes updates at 500ms. The collector cannot recover events missed while disconnected, and its memory buffer is cleared on process restart or explicit stop. No database persistence or historical backfill is claimed.
- The UI includes the isolated liquidation estimator and a live liquidation tape. No trading permissions or order endpoints are involved.


## PostgreSQL persistence for liquidation events
- PostgreSQL is an optional durable store configured through `DATABASE_URL`; the API uses SQLAlchemy's async engine with asyncpg. Without the setting, the existing bounded in-memory behavior remains available.
- `liquidation_events` stores normalized exchange event fields using Decimal-compatible `NUMERIC(38,18)` columns. `event_id` is the primary key for idempotent inserts; a composite provider/symbol/event-time index supports recent-event reads.
- Alembic migration `0001_liquidation_events` creates the table and index. Docker Compose starts PostgreSQL with a named volume, waits for database health, applies migrations, then starts the single-worker API.
- The collector updates its in-memory deque first and attempts persistence asynchronously. A database error is recorded as a sanitized exception class and does not terminate the exchange WebSocket loop. Once the database recovers, subsequent events are persisted; missed events during the outage are not backfilled.
- The authenticated snapshot endpoint reads durable rows when configured and available; it falls back to the collector's bounded in-memory snapshot if the database is unavailable. Database shutdown is included in API lifespan cleanup.
- This is event persistence only. General OHLCV, funding, open-interest, and long/short-ratio history are still not stored in PostgreSQL.
