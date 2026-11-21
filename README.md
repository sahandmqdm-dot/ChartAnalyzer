# ChartAnalyzer Next

A local, read-only quantitative market research MVP. It includes a FastAPI backend, signed-cookie authentication, provider-neutral Spot OHLCV ingestion, a small React workspace, and automated checks.

> **Scope and safety:** This is a single-admin, single-process research MVP. It is not a live-trading system and is not production-hardened. Do not expose it directly to the public internet or connect trading credentials. Persistent users/sessions, shared rate limits, audit logging, database-backed market history, streaming, portfolio-level risk controls, and an independent security review are not implemented.

## Features

- FastAPI API with health and authentication endpoints.
- HMAC-signed session cookies, CSRF-protected logout, and basic login throttling.
- Validated OHLCV candle domain model with UTC timestamps, Decimal values, OHLC invariants, and closed/open state.
- Binance Spot and Bybit Spot public market-data adapters behind one provider protocol.
- Provider selection using `MARKET_DATA_PROVIDER` (`binance_spot` or `bybit_spot`).
- Bounded historical pagination, transient-error retries, sanitized provider errors, and `closed_only=true`.
- Process-local asynchronous TTL/LRU cache with single-flight request coalescing.
- React + TypeScript workspace for login, selecting symbol/timeframe, viewing a close-price line chart, and inspecting recent OHLCV rows.
- Docker Compose for a loopback-bound, single-worker API container.

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

The API binds to 127.0.0.1:8000 and runs one worker because sessions, login throttling, and the candle cache are process-local. The React development UI is run separately using the steps above.

## API

```http
GET /api/v1/health
POST /api/v1/auth/login
GET /api/v1/auth/me
POST /api/v1/auth/logout
GET /api/v1/market-data/candles?symbol=BTCUSDT&interval=1h&limit=300&closed_only=true
GET /api/v1/market-data/candles/history?symbol=BTCUSDT&interval=1h&start_time=2026-01-01T00:00:00Z&end_time=2026-01-02T00:00:00Z
GET /api/v1/backtest/sma-crossover?symbol=BTCUSDT&interval=1h&start_time=2026-01-01T00:00:00Z&end_time=2026-02-01T00:00:00Z&fast_period=10&slow_period=30
```

Market-data endpoints require a valid session. Public exchange endpoints do not require exchange API keys. The history endpoint requires timezone-aware bounds and enforces a maximum of 10,000 candles. The cache has a 15-second TTL and 512-key bound; it is per process and not durable.

## Verification

```sh
cd backend
ruff check .
pytest
cd ../frontend
npm install
npm run build
```

CI runs backend lint/tests and the frontend production build. Exchange API tests use mocked HTTP responses and do not depend on live exchange availability.

## Known limitations

- One configured administrator; no user management or role-based access control.
- Login throttling, session revocation, and caching are in-memory and reset on restart.
- One worker per API instance; no shared Redis cache or distributed rate limiter.
- No PostgreSQL history store, WebSocket feed, portfolio-level risk engine, or order execution.
- Exchange interval support differs; Bybit Spot does not provide the 3-day or 8-hour intervals; unsupported intervals return a validation error.
- No production deployment hardening, observability stack, backup/restore, or external penetration test.

## Project layout

- `backend/app/auth.py`: local authentication/session handling.
- `backend/app/market_data/`: candle domain, providers, registry, cache, and API routes.
- `backend/tests/`: deterministic backend tests.
- `frontend/src/`: React research workspace.
- `docs/ARCHITECTURE.md`: boundaries and technical decisions.


## SMA crossover backtest

The authenticated `GET /api/v1/backtest/sma-crossover` endpoint runs a deterministic, long-only SMA crossover over historical closed candles. Signals are calculated from completed closes and executed at the following candle open; any open position is liquidated at the final close. Configure `fast_period`, `slow_period`, `initial_equity`, `fee_rate`, and `slippage_rate` as query parameters. This is a research tool, not a live-trading system. The engine marks open positions to market at each candle close, reports an equity curve, maximum drawdown, profit factor, expectancy, average win/loss, and exposure. It does not model funding, spread, partial fills, taxes, exchange-specific fee tiers, or intrabar price paths. Drawdown is based on candle-close marks, not tick-level liquidation prices.
