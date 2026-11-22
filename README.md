# ChartAnalyzer Next

A local, read-only quantitative market research MVP. It includes a FastAPI backend, signed-cookie authentication, provider-neutral Spot OHLCV and read-only linear-perpetual market snapshots, a small React workspace, and automated checks, with optional PostgreSQL persistence for observed liquidation events.

> **Scope and safety:** This is a single-admin, single-process research MVP. It is not a live-trading system and is not production-hardened. Do not expose it directly to the public internet or connect trading credentials. Persistent users/sessions, shared rate limits, audit logging, durable general market history, portfolio-level risk controls, and an independent security review are not implemented. Liquidation events can be persisted in PostgreSQL when configured.

## Features

- FastAPI API with health and authentication endpoints.
- HMAC-signed session cookies, CSRF-protected logout, and basic login throttling.
- Validated OHLCV candle domain model with UTC timestamps, Decimal values, OHLC invariants, and closed/open state.
- Binance Spot and Bybit Spot public market-data adapters behind one provider protocol.
- Provider selection using `MARKET_DATA_PROVIDER` (`binance_spot` or `bybit_spot`).
- Read-only Binance USDⓈ-M and Bybit linear-perpetual overview: mark/index price, current funding rate, open interest, notional open interest, and next funding time.
- Historical derivatives series for funding rate, open interest, and global long/short account ratio on Binance USDⓈ-M and Bybit Linear.
- Historical linear-perpetual Mark Price OHLC candles from Binance USDⓈ-M and Bybit V5, normalized to UTC and Decimal values.
- Bounded historical pagination, transient-error retries, sanitized provider errors, and `closed_only=true`.
- Process-local asynchronous TTL/LRU cache with single-flight request coalescing.
- React + TypeScript workspace for login, selecting symbol/timeframe, viewing a close-price line chart, and inspecting recent OHLCV rows.
- Docker Compose with PostgreSQL 16, a persistent named volume, health-gated API startup, and Alembic migrations.
- Optional PostgreSQL persistence for normalized liquidation events with unique event IDs, duplicate-safe inserts, indexed provider/symbol/time queries, and an explicit retention cleanup command; live streams continue with bounded memory fallback if storage is unavailable.
- Deterministic Spot and linear-perpetual backtesting with funding cashflows, isolated-margin accounting, Mark Price liquidation checks, liquidation-fee reserve estimates, bankruptcy/fill diagnostics, and explicit OHLC chronology assumptions.
- Isolated Futures risk estimation with configurable maintenance-margin tiers, initial-margin and close-fee reserves, bankruptcy-price estimate, and signed Mark Price distance to estimated liquidation.
- Multi-timeframe market-structure and Futures analysis endpoints; research outputs remain diagnostics and do not place orders.

## Requirements

- Python 3.12+
- Node.js 20+
- Docker Compose (optional)

## Local setup

1. Copy `.env.example` to `.env`.
2. Replace `APP_SECRET` with a cryptographically random value of at least 32 characters.
3. Replace `ADMIN_PASSWORD` with a strong unique password.
4. Choose `MARKET_DATA_PROVIDER=binance_spot` or `MARKET_DATA_PROVIDER=bybit_spot`.

Start the API:

```sh
cd backend
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# Linux/macOS: source .venv/bin/activate
pip install -e '.[dev]'
uvicorn app.main:app --reload
```

In a second terminal, start the UI:

```sh
cd frontend
npm install
npm run dev
```

Open http://127.0.0.1:5173. The API health endpoint is http://127.0.0.1:8000/api/v1/health. Development API docs are at http://127.0.0.1:8000/docs.

## Docker Compose

From the repository root, after creating and configuring `.env`:

```sh
docker compose up --build -d
docker compose ps
docker compose logs -f api
```

Compose starts PostgreSQL with a named persistent volume, waits for its health check, runs `alembic upgrade head`, and then starts the API. The API binds to 127.0.0.1:8000 and runs one worker because sessions, login throttling, the candle cache, and the live WebSocket collectors are process-local. The React development UI is run separately using the steps above.

## API

```http
GET /api/v1/health
POST /api/v1/auth/login
GET /api/v1/auth/me
POST /api/v1/auth/logout
GET /api/v1/market-data/candles?symbol=BTCUSDT&interval=1h&limit=300&closed_only=true
GET /api/v1/market-data/candles/history?symbol=BTCUSDT&interval=1h&start_time=2026-01-01T00:00:00Z&end_time=2026-01-02T00:00:00Z
GET /api/v1/derivatives/overview?symbol=BTCUSDT
GET /api/v1/derivatives/history?symbol=BTCUSDT&period=1h&limit=100
GET /api/v1/derivatives/mark-price-history?symbol=BTCUSDT&period=1h&limit=100
GET /api/v1/derivatives/analysis?symbol=BTCUSDT&period=1h
GET /api/v1/derivatives/risk-tiers?symbol=BTCUSDT
POST /api/v1/derivatives/risk-estimate
POST /api/v1/derivatives/liquidations/start?symbol=BTCUSDT
GET /api/v1/derivatives/liquidations?symbol=BTCUSDT&limit=100
GET /api/v1/derivatives/liquidations/heatmap?symbol=BTCUSDT&window=24h&bucket_minutes=15&price_bins=24&provider=all&side=all
GET /api/v1/derivatives/liquidations/clusters?symbol=BTCUSDT&window=24h&price_bins=40&min_strength_pct=35&min_events=2&include_market_context=true
GET /api/v1/derivatives/mark-price-history?symbol=BTCUSDT&period=1h&limit=100
DELETE /api/v1/derivatives/liquidations/stop?symbol=BTCUSDT
GET /api/v1/backtest/sma-crossover?symbol=BTCUSDT&interval=1h&start_time=2026-01-01T00:00:00Z&end_time=2026-02-01T00:00:00Z&fast_period=10&slow_period=30
```

Market-data and derivatives endpoints require a valid session. The derivatives overview is a current linear-perpetual snapshot. The derivatives history endpoint returns bounded funding-rate, open-interest, and global long/short account-ratio series for common periods (`5m`, `15m`, `30m`, `1h`, `4h`, `1d`). The derivatives analysis endpoint combines recent futures Mark Price returns with funding, open-interest, and account-ratio series to describe positioning, funding bias, and rule-based squeeze conditions; it is not a trading signal. It uses the same configured exchange family as Spot. Exchange retention windows and returned sample counts differ; Binance open-interest history is limited to the latest month and its global long/short ratio to the latest 30 days. The separate mark-price-history endpoint returns up to 200 historical linear-perpetual mark-price OHLC candles for `5m`, `15m`, `30m`, `1h`, `4h`, and `1d`; it is authenticated, read-only, and cached for 30 seconds. Candle completion is explicitly marked. The intelligence endpoint uses futures Mark Price candles for its price/OI interpretation. The risk calculator estimates isolated linear-position liquidation levels from supplied exchange risk tiers. Bybit tiers can be fetched from its public V5 API; Binance leverage brackets require a signed account API request and must be supplied from a verified account response. The authenticated liquidation stream controls connect to Binance USDⓈ-M `@forceOrder` or Bybit V5 `allLiquidation.{symbol}` public WebSocket feeds. They normalize the exchange-side semantics, executed quantity/price versus bankruptcy price, and quote notional. The collector is on-demand and keeps a bounded 2,000-event in-memory buffer per symbol; data is lost on process restart and there is no historical backfill. When `DATABASE_URL` is configured, normalized events are also inserted into PostgreSQL using the event ID as a deduplication key and queried from durable storage; `LIQUIDATION_RETENTION_DAYS` defaults to 90, and old rows are deleted only when the explicit maintenance command `docker compose exec api python -m app.maintenance prune-liquidations` is run (or run the module locally with `DATABASE_URL` set); if PostgreSQL becomes unavailable, the stream remains active and the API serves the bounded in-memory snapshot until storage recovers. A database outage can therefore leave gaps that are not backfilled. Binance publishes a latest liquidation snapshot per symbol per one-second interval, while Bybit pushes liquidation updates at 500ms; these are exchange stream snapshots, not a complete liquidation archive. There is not yet an order-book liquidation heatmap or futures-aware backtesting. Public exchange endpoints do not require exchange API keys. The history endpoint requires timezone-aware bounds and enforces a maximum of 10,000 candles. The cache has a 15-second TTL and 512-key bound; it is per process and not durable.

## Verification

```sh
cd backend
ruff check .
pytest
cd ../frontend
npm install
npm run build
```

CI runs backend lint/tests against PostgreSQL 16, applies the real Alembic migration, and verifies durable insert, duplicate handling, Decimal round-trip, and explicit retention cleanup; the frontend production build also runs. Exchange API tests use mocked HTTP responses and do not depend on live exchange availability.

## Known limitations

- One configured administrator; no user management or role-based access control.
- Login throttling, session revocation, and caching are in-memory and reset on restart.
- One worker per API instance; no shared Redis cache or distributed rate limiter.
- No general-purpose PostgreSQL market-history store, shared/distributed WebSocket infrastructure, cross/portfolio margin engine, or order execution. PostgreSQL persistence currently covers observed liquidation events, not all market history. Liquidation streams are process-local and use bounded in-memory fallback when persistence is unavailable.
- Futures liquidation and risk outputs are estimates, not exchange-guaranteed prices. They do not reproduce each exchange's complete maintenance tiers, account-level collateral, open orders, insurance fund, ADL, or exact liquidation execution. Funding-aware Futures backtesting is implemented with documented OHLC approximations; it is not tick-level replay.
- No full order-book depth reconstruction/heatmap, production observability stack, backup/restore runbook, or external penetration test. Cross-process coordination and shared rate limiting remain future work.
- Exchange interval support differs; Bybit Spot does not provide the 3-day or 8-hour intervals; unsupported intervals return a validation error.
- No production deployment hardening, observability stack, backup/restore, or external penetration test.

## Project layout

- `backend/app/auth.py`: local authentication/session handling.
- `backend/app/market_data/`: candle and futures domain, Spot/derivatives providers, registry, cache, and API routes.
- `backend/tests/`: deterministic backend tests.
- `frontend/src/`: React research workspace.
- `docs/ARCHITECTURE.md`: boundaries and technical decisions.
- `docs/FEATURE_PARITY_AUDIT_FA.md`: feature parity and migration priorities against the legacy `main` branch.


## SMA crossover backtest

The authenticated `GET /api/v1/backtest/sma-crossover` endpoint runs a deterministic, long-only SMA crossover over historical closed candles. Signals are calculated from completed closes and executed at the following candle open; any open position is liquidated at the final close. Configure `fast_period`, `slow_period`, `initial_equity`, `fee_rate`, and `slippage_rate` as query parameters. This is a research tool, not a live-trading system. The engine marks open positions to market at each candle close, reports an equity curve, maximum drawdown, profit factor, expectancy, average win/loss, and exposure. It does not model funding, spread, partial fills, taxes, exchange-specific fee tiers, or intrabar price paths. Drawdown is based on candle-close marks, not tick-level liquidation prices.
