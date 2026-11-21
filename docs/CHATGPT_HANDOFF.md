# ChartAnalyzer — ChatGPT Handoff

This document is the handoff brief for continuing the project in another ChatGPT account. Read it together with `README.md`, `docs/ARCHITECTURE.md`, and `docs/SECURITY_BASELINE.md`.

## Repository and branch

- Repository: https://github.com/sahandmqdm-dot/ChartAnalyzer
- Active development branch: `chartanalyzer-next-foundation`
- Branch URL: https://github.com/sahandmqdm-dot/ChartAnalyzer/tree/chartanalyzer-next-foundation
- Keep `main` untouched. Do not merge or deploy unless the owner explicitly asks.
- The work is an isolated foundation in a branch of the existing repository, not a separate repository.

## Product direction

ChartAnalyzer is being developed as a modular chart-analysis platform. The intended bounded contexts are Identity, Market Data, Quant, Backtest, Portfolio/Risk, Research, AI, and UI. The current priority is a reliable market-data foundation, not live trading.

Do not expose this application to the public internet or use it for live trading. It is not production-ready.

## Current implementation

### Backend and identity
- FastAPI backend.
- Environment-based settings in `backend/app/settings.py`.
- HMAC-signed sessions, PBKDF2 password verification, login throttling, CSRF double-submit on logout, and in-memory session revocation.
- Routes include health and auth endpoints.
- Important known limitations: one bootstrap admin, in-memory throttling and revocation reset on restart, no database-backed user/session store, no durable audit trail, and incomplete authorization hardening.

### Market data
- Provider-neutral `MarketDataProvider` protocol in `backend/app/market_data/provider.py`.
- Registry in `backend/app/market_data/registry.py`; Binance Spot is the current default provider.
- Canonical `Candle` model uses Decimal OHLCV, UTC-aware open/close timestamps, and an explicit `is_closed` flag.
- `Symbol` normalizes case/whitespace and validates supported characters.
- `Timeframe` defines canonical exchange intervals, including a calendar-month interval that has no fixed seconds duration.
- Binance public Spot Klines adapter validates symbol, timeframe, limits, and time bounds; converts data to Decimal/UTC; normalizes provider errors.
- Authenticated endpoint: `GET /api/v1/market-data/candles`.
- Process-local async TTL/LRU cache: 15 seconds, up to 512 keys. It is not shared across worker processes.
- Binance adapter classifies each candle as open or closed using the exchange-provided Kline close time and an injectable UTC clock; one clock snapshot is used per response batch.
- Bounded historical pagination is implemented in the Binance adapter with page limits, a total candle cap, close-time-based cursor advancement, duplicate filtering, and a no-progress guard.
- Authenticated `GET /api/v1/market-data/candles/history` exposes bounded history retrieval. It requires timezone-aware start/end values, limits each page to 1000, and caps total candles at 10000.
- Bounded retry/backoff was added for transport errors, HTTP 429, and selected 5xx responses. Numeric `Retry-After` is honored (capped); otherwise exponential delay is used. Client errors are not retried. The sleep function is injectable for deterministic tests.
- Tests use `httpx.MockTransport`; they should not rely on live Binance.

## Latest work and CI

Recent completed work includes canonical Symbol/Timeframe models and their tests. Before the retry work, the latest confirmed successful CI was:
https://github.com/sahandmqdm-dot/ChartAnalyzer/actions/runs/44926881301

Retry implementation commits:
- `8a05ae6` — bounded Binance retries
- `8832f74` — retry/backoff tests
- `84ee503` — architecture documentation
- `4b61e7c` — Ruff import correction

The initial retry commit's CI failed only at Ruff due to importing Awaitable/Callable from typing rather than collections.abc. That import was corrected in `4b61e7c`. Check the latest GitHub Actions run on `chartanalyzer-next-foundation` before claiming that CI passes:
https://github.com/sahandmqdm-dot/ChartAnalyzer/actions

## Development rules

1. Work only on `chartanalyzer-next-foundation`.
2. Never change `main`, merge, deploy, or publish without explicit owner approval.
3. Make small, reviewable commits.
4. Add deterministic tests for every behavior change.
5. Run/inspect GitHub Actions after changes; never claim success while CI is queued, running, or failed.
6. Update `docs/ARCHITECTURE.md` when architecture or implemented behavior changes.
7. Do not introduce API keys for public Binance Klines.
8. Do not add live trading or order execution. Keep the system analysis/backtesting only.
9. Treat secrets carefully; never commit real `.env` files, credentials, tokens, or private data.
10. Prefer explicit domain abstractions and provider-neutral contracts; avoid exchange-specific details leaking into domain models.

## Recommended next steps

Work through these in order, checking existing code and tests before implementation:

1. Verify the latest CI after commit `4b61e7c`; fix any remaining failures.
2. Review retry semantics and add coverage for transport exceptions and exhausted retry attempts. Consider parsing HTTP-date Retry-After only if justified by provider behavior.
3. Candle completeness semantics are implemented with `close_time` and `is_closed`; next, ensure API consumers and future backtests explicitly filter open candles where appropriate.
4. Authenticated historical-candles API is implemented; verify CI and then plan persistent storage and synchronization.
5. Strengthen quality validation and document gap semantics per timeframe, especially calendar month.
6. Only after the ingestion contract stabilizes, consider persistence (PostgreSQL) and shared cache (Redis).
7. Later: WebSocket streaming, UI charting, quant indicators, backtesting, portfolio/risk, research and AI assistance. Keep AI out of deterministic execution paths.

## Ready-to-use instruction for the next ChatGPT

You are continuing the ChartAnalyzer engineering project. First read this file and the README, architecture, and security baseline documents in the repository. Inspect the latest GitHub Actions status and current branch before editing. Continue only on `chartanalyzer-next-foundation`; do not touch `main`, merge, deploy, or add live trading. The project currently has a FastAPI/auth foundation and a provider-neutral market-data layer with Binance Spot Klines, canonical Candle/Symbol/Timeframe models, explicit open/closed candle metadata, a process-local TTL/LRU cache, and bounded retry/backoff. Verify CI after the latest retry import fix (`4b61e7c`). Verify CI, then continue with bounded historical pagination and explicit open-candle handling at API/backtest boundaries. Make small commits, update architecture docs, and report exact commit hashes and CI links. Do not claim production readiness.
