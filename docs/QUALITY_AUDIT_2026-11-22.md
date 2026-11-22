# ChartAnalyzer Next — Quality Audit (2026-11-22)

Scope: structural review of the `chartanalyzer-next-foundation` branch. This is a code-reading audit; it is not a penetration test, complete local test run, or proof of correctness.

## Executive assessment

The branch has a useful separation between API, market-data providers, backtesting, and frontend. However, it remains a development scaffold and should **not be exposed to the public Internet or used for live trading** until the high-severity items below are resolved.

## Findings

### Q-001 — Process-local auth controls (High)
**Evidence:** `backend/app/auth.py` stores login attempt windows and revoked session IDs in process-memory collections. The single bootstrap user model is also in scope.
**Impact:** Restart resets throttling and revocation. Multiple workers can bypass rate limits and have inconsistent logout/revocation state. Revoked session IDs can grow without bounded cleanup.
**Remediation:** Move sessions, revocations, and login rate limits to a shared persistent store (Redis/DB); add expiry/TTL; make limits proxy-aware only when trusted proxy configuration is explicit.

### Q-002 — Origin/Host validation missing on authenticated state-changing endpoints (High)
**Evidence:** Login/logout handling and cookie auth are visible in `backend/app/main.py`; CSRF double-submit is enforced for logout but audit has not established coverage for every state-changing endpoint.
**Impact:** Cookie-based sessions can be exposed to cross-origin request risks if browser-origin protections are incomplete.
**Remediation:** Central middleware/dependency for unsafe HTTP methods: validate Origin (and optionally Referer fallback) against explicit allowlist; enforce CSRF for all authenticated mutations. Add regression tests for missing/invalid CSRF and untrusted origins.

### Q-003 — API schema exposure depends on environment string (Medium)
**Evidence:** `docs_url` and `openapi_url` are disabled unless `environment == "development"`.
**Impact:** An incorrectly configured environment may expose developer documentation, or make docs behavior inconsistent.
**Remediation:** Add explicit configuration and test both development and production app settings. Confirm environment values are normalized and fail closed.

### Q-004 — Unpinned dependencies and absent lockfiles (Medium)
**Evidence:** Backend dependencies use broad version ranges; frontend package metadata does not include a lockfile in inspected files; CI uses `npm install`.
**Impact:** Rebuilds may resolve different dependency versions, reducing reproducibility and complicating vulnerability triage.
**Remediation:** Commit a frontend lockfile, use `npm ci`, lock Python dependencies (pip-tools/uv lock), and add dependency scanning (pip-audit/npm audit).

### Q-005 — CI coverage gaps (Medium)
**Evidence:** CI runs backend tests and frontend build. Ruff is currently scoped to Futures-risk files due to unrelated existing lint debt. CI lacks a full security scan and separate integration test coverage for all provider adapters.
**Impact:** Regressions outside the current lint scope and dependency vulnerabilities may not be caught.
**Remediation:** Gradually expand lint scope; add pip-audit, Bandit, npm audit, provider contract tests, and test DB migration lifecycle.

### Q-006 — Futures live risk snapshot, not continuous exchange stream (Medium)
**Evidence:** v3 uses request-time Mark Price and frontend polling. Exchange tier data is cached for five minutes.
**Impact:** The interface can be stale between polls, while tier cache age differs from Mark Price age. It does not provide push alerting or exchange-exact liquidation.
**Remediation:** WebSocket price source, server-side monitoring, stale-data circuit breaker, timestamp skew limits, and explicit confidence/status UI.

### Q-007 — Backtest realism needs explicit guardrails (High for trading decisions)
**Evidence:** Current OHLC-based backtest architecture and existing docs note approximations.
**Impact:** Candle data cannot resolve intrabar event ordering. Funding, fees, slippage, gaps, partial fills, and liquidation behavior can materially affect results.
**Remediation:** Add conservative intrabar execution assumptions; test gap/stop/liquidation collisions; make costs explicit; validate with out-of-sample and walk-forward; label OHLC simulation vs tick-level.

### Q-008 — Database lifecycle and operational safeguards (Medium)
**Evidence:** Async engine is created lazily and disposed on app shutdown. Persistence coverage and migration/backup behavior require deeper integration review.
**Impact:** DB unavailable/slow conditions and deployment/migration errors may cause reliability issues.
**Remediation:** Add startup connectivity checks where DB is required, readiness endpoint, timeouts, migrations test, backup/restore drill, and connection pool configuration by deployment mode.

## Release gates

Do not label the branch production-ready until:
- all tests are green and meaningful failures are fixed (not skipped or hidden);
- auth/session/rate-limit state is shared and bounded;
- origin/CSRF controls cover all authenticated mutations;
- dependency lockfiles and security scans are enforced;
- backtest limitations and conservative assumptions are surfaced in UI/report outputs;
- deployment secrets, migration, backup, monitoring, and recovery are documented and tested.

## Audit limitations

Only source files inspected through GitHub were reviewed. No local checkout, full static analysis, live exchange tests, penetration test, or comprehensive test suite was run during this audit. Findings are risk hypotheses grounded in inspected code and should be validated with targeted tests.


## CI failure triage (run 45034397263)

Latest inspected GitHub Actions run: frontend build passed; backend pytest failed with **7 failures and 274 passing tests** (Ruff passed).

The failures are not yet resolved. Initial source/test comparison suggests several test expectations or fixtures are inconsistent with the current accounting/signal model; these must be validated rather than changed merely to obtain a green build:

- `test_futures_sma_enters_long_and_closes_at_end_of_data`: the crossover is detected at the final candle in its five-candle fixture, so entry and end-of-data close occur at the same flat price. The fixture does not provide a post-entry price move to justify the expected profit.
- `test_long_position_is_liquidated_on_mark_price_low`, `test_intrabar_adverse_mark_price_is_included_in_max_drawdown`, and `test_intrabar_funding_credit_cannot_rescue_prior_liquidation`: the short fixture length/strategy timing appears to leave the signal at the final candle, so no position exists during the candle whose Mark Price is modified. Confirm signal timing and revise fixtures to include an execution candle followed by the stressed candle, preserving a no-lookahead rule.
- `test_realized_pnl_includes_entry_and_exit_fees`: with entry fee 1.00, gross PnL 100.00, and exit fee 1.10, realized PnL is 97.90 (100 - 1 - 1.10); the test's 98.90 expectation is arithmetically inconsistent. Wallet balance should be 10,097.90 under the current wallet accounting.
- `test_liquidation_settlement_updates_wallet_and_clears_position`: settlement caps isolated-position loss at isolated margin (500), including liquidation fee, so wallet 9,499 is consistent with the current model. The test's direct subtraction of gross loss and fee from wallet (9,498.50) conflicts with this cap. Review whether that is the intended model before adjusting either test or implementation.
- `test_bankruptcy_price_moves_with_funding_but_not_liquidation_fee_reserve`: a +10 margin adjustment for a long increases isolated equity and should lower (not raise) estimated bankruptcy price from 500 to 490 under the current formula. The test expects 510, which appears to have the funding sign reversed.

This triage is source-level and derived from the CI logs; it is **not a substitute for running tests locally**. The account settlement model, event validity, and strategy candle alignment need targeted tests before any expectation is changed. CI run link: https://github.com/sahandmqdm-dot/ChartAnalyzer/actions/runs/45034397263
