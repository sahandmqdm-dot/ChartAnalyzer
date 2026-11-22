# Architecture

Bounded contexts: Identity, Market Data, Quant, Backtest, Portfolio/Risk, Research, AI, UI.

Dependency direction: API -> application -> domain. Infrastructure implements interfaces. Domain must not depend on web frameworks, databases, exchange SDKs, or LLM libraries.

MVP target: local research shell, market-data ingestion, charting, and deterministic single-strategy backtesting. Current branch implements the local shell, basic close-price chart, deterministic long-only SMA crossover backtest, public derivatives snapshots, and on-demand liquidation streams. PostgreSQL persistence is implemented for liquidation events only; general historical market-data storage remains future work. No live trading.


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
- The exchange streams are snapshots, not a complete historical liquidation tape: Binance emits at most the latest liquidation order per symbol per 1-second interval; Bybit pushes updates at 500ms. The collector cannot recover events missed while disconnected, and its memory buffer is cleared on process restart or explicit stop. PostgreSQL persistence stores events received while connected, but there is no exchange historical backfill.
- The UI includes the isolated liquidation estimator and a live liquidation tape. No trading permissions or order endpoints are involved.


## PostgreSQL persistence for liquidation events
- PostgreSQL is an optional durable store configured through `DATABASE_URL`; the API uses SQLAlchemy's async engine with asyncpg. Without the setting, the existing bounded in-memory behavior remains available.
- `liquidation_events` stores normalized exchange event fields using Decimal-compatible `NUMERIC(38,18)` columns. `event_id` is the primary key for idempotent inserts; a composite provider/symbol/event-time index supports recent-event reads.
- Alembic migration `0001_liquidation_events` creates the table and index. Docker Compose starts PostgreSQL with a named volume, waits for database health, applies migrations, then starts the single-worker API.
- The collector updates its in-memory deque first and attempts persistence asynchronously. A database error is recorded as a sanitized exception class and does not terminate the exchange WebSocket loop. Once the database recovers, subsequent events are persisted; missed events during the outage are not backfilled.
- The authenticated snapshot endpoint reads durable rows when configured and available; it falls back to the collector's bounded in-memory snapshot if the database is unavailable. Database shutdown is included in API lifespan cleanup.
- This is event persistence only. General OHLCV, funding, open-interest, and long/short-ratio history are still not stored in PostgreSQL.
- `GET /api/v1/derivatives/liquidations/heatmap` aggregates persisted events inside PostgreSQL by UTC time bucket and price band, with exchange and long/short filters. Only grouped cells and summary metadata cross the database boundary; the raw event rows are not materialized in the API process. Queries are bounded to 50,000 records and mark capped responses as truncated.
- Migration `0002_liquidation_query_indexes` adds a symbol/time/side composite index for heatmap filters and an event-time index for retention cleanup.
- `GET /api/v1/derivatives/liquidations/clusters` groups adjacent price bands when each band's observed notional reaches a configurable fraction of the strongest band and the minimum event count. It returns descriptive clusters, long/short split, event counts, notional and last observed time. Optional futures context includes the latest funding, OI and long/short account ratio; this is context only, not a causal model or forecast.
- The frontend can overlay observed price bands on the configured venue's futures Mark Price OHLC history, calculate distance from the current Mark Price to the nearest observed cluster, and poll cluster/overview/mark-price data every 30 seconds while monitoring is enabled. Browser-session alerts are generated when a matched cluster's observed notional increases by at least 50% between consecutive snapshots. They are ephemeral UI annotations, not durable alert records, push notifications, or future liquidation estimates. The result is a visualization of observed feed events, not future liquidation levels or a complete market-wide tape.
- `LIQUIDATION_RETENTION_DAYS` defaults to 90 days. Cleanup is intentionally explicit via `python -m app.maintenance prune-liquidations`; ingestion never automatically deletes historical rows.


## Market structure intelligence
- `GET /api/v1/derivatives/market-structure` analyzes authenticated, read-only linear-perpetual Mark Price OHLC candles from the configured Binance USDⓈ-M or Bybit Linear provider.
- The deterministic first release detects confirmed swing highs/lows using a symmetric pivot window, describes a simple HH/HL or LH/LL trend state, identifies close-through BOS/CHoCH events, wick-through-and-reject liquidity sweeps, and three-candle Fair Value Gaps. Only closed candles are used.
- Pivots are confirmed only after the configured number of candles on the right side have closed. The latest pivot is therefore delayed by design; this avoids treating an unconfirmed local extreme as a confirmed swing.
- FVG lifecycle is tracked across subsequent closed candles: active (no revisit), partially_filled (price enters the zone), filled (price reaches the far edge), or invalidated (a candle closes beyond the far edge). The first mitigation timestamp and invalidation timestamp are retained in the response. For bullish gaps the far edge is the lower bound; for bearish gaps it is the upper bound. A close exactly at the far edge counts as filled, not invalidated.
- Order Blocks are linked to confirmed swing breaks: for each confirmed swing high/low, the analyzer finds the first subsequent candle close through that level and selects the last opposing candle among at most five candles before the break, after pivot confirmation. The full source candle low/high is the zone. Bullish blocks arise from breaks above swing highs; bearish blocks from breaks below swing lows. Status is active until price re-enters the zone (mitigated), and invalidated only if a later candle closes beyond the far edge. A later invalidation supersedes the mitigated state while preserving the first mitigation timestamp.
- These are explicit heuristic conventions, not proof of institutional orders or a standardized SMC definition. Equal highs/lows, multi-timeframe confluence, and volume-profile context are not included.
- The rules are descriptive conventions, not a standardized SMC definition, predictive model, or trade recommendation. The endpoint is cached for 30 seconds and returns bounded lists; it does not persist derived signals or place orders.

- Automated coverage for the first lifecycle implementation checks first-touch timestamps, full-fill versus close-through invalidation, and Order Block creation, mitigation, and invalidation; the frontend exposes these states without generating trade actions.


## Multi-timeframe structure confluence
- `GET /api/v1/derivatives/confluence?symbol=BTCUSDT` compares `5m`, `15m`, `1h`, and `4h` market structure using the same configured Binance USDⓈ-M or Bybit Linear Mark Price adapter. The authenticated endpoint fetches each timeframe concurrently and each analysis independently excludes unclosed candles.
- Each timeframe reports its structural trend, qualitative directional bias, latest BOS/CHoCH, active bullish/bearish FVG and Order Block counts, closed-candle count, and evidence. Directional bias follows HH/HL or LH/LL trend; when the structure is range/neutral, a latest directional BOS/CHoCH may supply the bias. Liquidity sweeps and active zones are supporting evidence, not independent votes.
- Aggregate states are explicit: `aligned` when every available timeframe has the same directional bias; `partial_alignment` when at least two directional timeframes agree and the remaining timeframes are neutral; `conflicted` when bullish and bearish directional timeframes coexist; and `insufficient_directional_data` when fewer than two timeframes have directional bias. The endpoint never converts a conflict into a forced directional signal.
- Individual timeframe fetch failures are listed in `unavailable_timeframes`; available timeframes are still returned, and the response caveat marks the result incomplete. If every timeframe fails, the endpoint returns a sanitized 502 response. Results are cached for 30 seconds by provider, symbol, limit, and pivot window.
- The engine intentionally avoids an opaque numeric confidence score. It does not yet fuse funding/OI changes or liquidation clusters into the directional vote; those datasets have different sampling/retention semantics and remain separate market-context evidence until a validated time-alignment rule is designed. No trading actions are exposed.
- Unit and route tests cover full alignment, partial alignment, conflict handling, insufficient directional data, authentication, symbol validation, and fetching all four intervals.


## Independent Spot and Futures provider selection
- `MARKET_DATA_PROVIDER` selects only the normalized Spot OHLCV adapter (`binance_spot` or `bybit_spot`).
- `FUTURES_MARKET_DATA_PROVIDER` independently selects the linear-perpetual adapter (`binance_usdm_futures` or `bybit_linear_futures`); it defaults to Binance USDⓈ-M to preserve the prior default behavior.
- The derivatives overview, historical funding/OI/account-ratio, Mark Price history, analysis, and market-pressure paths resolve the configured Futures provider. Switching Spot venues does not silently switch the derivatives venue.
- Binance-specific live-market and Open Interest polling collectors remain explicitly Binance USDⓈ-M integrations; they are not silently redirected to Bybit by the provider selector.
- Both settings are validated independently. Public market-data access remains read-only and requires no exchange credentials.


## Bybit live Futures stream and current Open Interest
- The configured `FUTURES_MARKET_DATA_PROVIDER` selects the live stream and Open Interest collector, independently of Spot.
- Binance USDⓈ-M keeps its combined mark-price, book-ticker, and aggregate-trade stream.
- Bybit uses the public V5 linear WebSocket endpoint with `tickers.{symbol}` and `publicTrade.{symbol}`. Ticker snapshots/deltas are merged into a bounded latest-value snapshot; the stream includes mark/index price, funding, open interest, and best bid/ask where supplied. Trade batches use the latest trade in the exchange-provided chronological batch.
- Bybit public WebSocket application pings are sent every 20 seconds; reconnect delay is bounded. Mark/funding observations are persisted at most once per second to avoid writing every 100ms ticker delta.
- Current Open Interest is polled every 60 seconds from Binance USDⓈ-M `/fapi/v1/openInterest` or Bybit V5 `/v5/market/tickers?category=linear`. Both are public read-only endpoints and need no exchange credentials.
- `open-interest-history` is a provider-filtered local observation history. It begins only after the selected collector runs; it is not exchange historical OI backfill. Binance and Bybit records are never mixed.
- Both live stream and Open Interest polling have explicit authenticated stop endpoints and are also stopped during API shutdown.


## Market-data health and staleness
- Authenticated `GET /api/v1/health/market-data` is observational only; it never starts or restarts collectors.
- The report is scoped to the selected `FUTURES_MARKET_DATA_PROVIDER` and lists each registered live stream and Open Interest poller with state, last observation, age, stale threshold, last error, and persistence state.
- Live streams are marked stale after 15 seconds without an observation. Open Interest uses the greater of 180 seconds or three configured polling intervals.
- Missing first observations are reported as `waiting_for_data`, not stale. Stopped feeds are reported as stopped and do not degrade an otherwise idle service.
- Overall status is `idle`, `starting`, `healthy`, or `degraded`; reconnecting, stale, future-clock-skewed, or persistence-unavailable feeds degrade the report.
- The UI refreshes this report while live feeds are enabled and shows per-feed age and persistence status.


## Safe live-feed lifecycle methods
- Starting and stopping live market or Open Interest collectors are state-changing operations. They use authenticated `POST` and `DELETE` requests with the double-submit `X-CSRF-Token` header.
- `GET /live-market` and `GET /open-interest-live` only return the current snapshot; they never start network activity.
- Liquidation stream start/stop endpoints use the same CSRF requirement. Read-only market, history, health, and snapshot endpoints remain GET requests.
- The React client sends the CSRF cookie value on lifecycle requests and polls snapshots separately.


## Resource limits and bounded authentication state
- Active Futures market streams, Open Interest pollers, and liquidation streams are capped independently per provider. Each cap defaults to 10 and is configurable through `MAX_ACTIVE_FUTURES_MARKET_STREAMS`, `MAX_ACTIVE_OPEN_INTEREST_COLLECTORS`, and `MAX_ACTIVE_LIQUIDATION_STREAMS` (range 1–100).
- Starting a new collector beyond its cap returns HTTP 429; an already-running collector for the same symbol can be reused without consuming another slot. Registries retain at most 500 entries per collector type and evict stopped entries when needed.
- Snapshot GET and stop DELETE routes do not create missing collector entries; only CSRF-protected start POST requests allocate a collector.
- Login attempt tracking retains at most 10,000 IP keys and evicts the least-recently-used entry when full. The five-attempts-per-minute limit remains per IP.
- Revoked session IDs are stored with their expiry and purged through an expiry heap after the session TTL, rather than remaining in process memory indefinitely.


## Futures backtest event ordering
- Funding events timestamped at or before a candle open are applied to a position carried into that candle before any signal-driven exit at the open.
- The opening Mark Price is checked for a gap liquidation before a strategy order can close or reverse the carried position. A position liquidated at the open cannot immediately re-enter on that same signal.
- If the carried position survives the open, signal transitions execute at the contract OHLCV open. The resulting position is then checked against the full closed Mark Price candle.
- Funding events timestamped inside the candle are applied only after the intrabar liquidation check because OHLC cannot establish which event occurred first. This remains a conservative simulation approximation.
