# ChartAnalyzer | Security, Integrity and Validation Audit

**Audit date:** 2026-11-20  
**Validated main commit:** `8772da1ea0d8d1797bccc123c94ad8418163603a`  
**GitHub Actions run:** `44722080675`  
**Review type:** automated CI evidence plus targeted source/contract review. This is not an independent penetration test, exchange certification or production-readiness certificate.

## Executive result

The latest mainline passes both Python test matrices, compilation, dependency auditing and Bandit medium/high gates. The prospective future-holdout collector and baseline comparison pipeline are now implemented, but **no prospective cohort has been registered and no future performance sample has been collected**. The Windows runner has not yet been smoke-tested on the user's machine.

The repository is suitable for controlled research use and further validation. It is not yet a production-certified financial decision system.

## Automated validation

| Check | Latest result |
|---|---|
| Python 3.12 test suite | **527 passed** |
| Python 3.13 test suite | **527 passed** |
| Coverage | **84%** on both Python versions |
| Python compilation | Passed on both Python versions |
| Dependency audit | `pip-audit`: no known vulnerabilities found |
| Bandit | **0 medium / 0 high**; 888 low-severity findings remain |
| SQLite resource lifecycle | Previously observed Python 3.13 unclosed-database warnings are resolved |
| Ruff | Advisory job remains non-blocking and reports **437 repository-wide diagnostics** |

The Ruff breakdown is UP006 (105), I001 (75), UP035 (53), BLE001 (49), UP045 (39), RUF046 (22), F401 (18), UP017 (15), TRY004 (10), RUF022 (9), RUF059 (5), F841 (6), C408 (4), SIM115 (4), SIM102 (3), RUF012 (3), UP031 (2), EXE001 (2), RUF007 (2), UP012 (2), PIE807 (2), FURB188 (1), FURB167 (1), FURB157 (1), RUF034 (1), S110 (1).

Ruff is not a clean result. Its current advisory configuration means these diagnostics do not fail the overall workflow. They must be addressed in controlled batches; do not apply a blind repository-wide rewrite to security-sensitive or financial logic.

## Prospective future-holdout capability

The following components are now present:

- `prospective_holdout.py`: append-only cohort, forecast and outcome ledger; frozen cohort settings; non-overlapping forecast horizons; hash-chain integrity; one-time final evaluation.
- `prospective_baselines.py`: zero-return, historical-unconditional and EWMA-Gaussian baselines, scored on the exact same realized outcomes as the candidate.
- `app.py`: register, status, tick and evaluate endpoints for the local collector.
- `tools/Register-ProspectiveHoldout.ps1`: Windows cohort registration helper.
- `tools/Invoke-ProspectiveHoldoutTick.ps1`: Windows collector tick for Task Scheduler.
- `docs/PROSPECTIVE_HOLDOUT_PROTOCOL.md`: protocol, timestamp semantics and operator instructions.

Integrity safeguards include server-computed configuration fingerprints, code hashes for the PMI, forecast-validation, research-provenance, market-data adapter, ledger and baseline components, the research-engine revision, closed-candle-only inputs, exact target-candle resolution, and fail-closed handling of invalid, gapped or off-grid OHLCV.

Three baselines are frozen at each forecast cutoff. Candidate-minus-baseline differences for quantile pinball loss, median MAE, central-80 coverage and interval width are evaluated with paired moving-block-bootstrap confidence intervals. Negative loss deltas favor the candidate; positive coverage deltas favor it. Interval width must be interpreted together with coverage.

### Important limits

1. The initial collector is **Spot-only** and supports Ramzinex or CCXT input.
2. The first outcome resolver uses the **same provider** as the input feed. This is future-only in time, but not independently sourced. A second provider must not be introduced until instrument identity, contract, quote currency, price basis and candle boundaries are proven equivalent.
3. No cohort has been registered, no future outcomes have been resolved, and no predictive edge or profitability is demonstrated.
4. The current PMI does not provide a calibrated directional probability; directional Brier scoring is therefore unavailable.
5. The Windows PowerShell scripts have not yet been executed on the user's Windows environment. The first run must be treated as a smoke test, not as validation evidence.
6. A cohort can become incomplete if the app or PC is offline, provider data is missing, or scheduled ticks are missed. Missing outcomes remain unresolved; the system must not fill them with nearby candles or synthetic prices.
7. The final report is descriptive and does not automatically promote a model. Any model change requires a new frozen cohort.

## Security controls reviewed

- The application defaults to loopback-only operation; LAN mode is explicit and requires a token.
- Host/origin checks, constant-time token comparison, bounded request handling and restrictive response headers are present.
- User-supplied UDF URLs are checked against configured allowlists; upstream redirects are disabled.
- CSV ingestion and research payload sizes are bounded.
- SQLite files and sidecars receive restrictive POSIX permissions; symlink database paths are rejected.
- MCP starts without a shell, uses a reduced child environment and does not inherit application provider secrets or the LAN token.
- MCP integration is read-only and does not expose order placement.
- The prospective collector uses read-only public market data and has no order-placement path.
- Holdout evaluation is one-time and integrity-checked before and after report creation.

See `SECURITY.md` for the threat model and operational guidance.

## Residual risk and production prerequisites

1. **No TLS in the built-in server.** LAN mode uses plain HTTP. Do not expose it directly to the public Internet; use a trusted network and an HTTPS reverse proxy with authentication and network controls.
2. **Shared LAN token.** There are no per-user roles or server-side session revocation. Loopback clients are trusted by design.
3. **No independent DAST or penetration test.** Bandit and pip-audit are automated checks, not proof that the application is vulnerability-free.
4. **No Windows runtime certification.** CI runs on Ubuntu. A Windows 11 smoke test is still required.
5. **No live provider certification.** Rate limits, symbol coverage, data entitlements, payload changes, missing candles and provider outages must be tested from the deployment environment.
6. **No profitability claim.** Bar-level forecasts and backtests do not fully model order-book depth, queue position, partial fills, market impact, exchange liquidation or every historical funding mechanic.
7. **Coverage gap.** 84% line coverage leaves 16% unexecuted by the current suite. High-risk orchestration, failure and platform-specific paths need further targeted tests.
8. **Static-analysis debt.** The 441 Ruff diagnostics and 888 Bandit low-severity findings require triage. They are not all confirmed vulnerabilities, but must not be silently ignored.
9. **CMI remains incomplete.** CMI-01/02 and the prospective evaluation infrastructure are present. Fundamental/macro, news research, human sentiment, knowledge graph, event impact and full scenario intelligence remain roadmap work.

## Required next steps

1. Run the Windows registration and collector scripts against the local app, initially with a small non-production test cohort.
2. Verify Ramzinex symbol mapping, closed-candle boundaries, data freshness, gaps, off-grid intervals and exact outcome resolution on the actual machine.
3. Register a future cohort only after that smoke test; keep the PMI and baseline fingerprints frozen for the entire collection.
4. Collect the pre-registered non-overlapping sample, resolve every target outcome and consume the report once.
5. Review candidate-versus-baseline paired confidence intervals without tuning on the holdout. If the result is inconclusive or weak, do not claim edge.
6. Add a genuinely independent outcome feed only after exact instrument and price-basis equivalence is validated.
7. Complete CMI-03 through CMI-08 with point-in-time provenance and out-of-sample acceptance tests.
8. Run an independent security review and Windows deployment test before production exposure.

**Conclusion:** current CI supports controlled development and a first local prospective-collection smoke test. It does not yet support a claim that ChartAnalyzer has demonstrated predictive edge, independent-source validation or production readiness.
