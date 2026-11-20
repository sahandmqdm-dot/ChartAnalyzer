# ChartAnalyzer | Security and Repository Validation Snapshot

**Audit date:** 2026-11-20  
**Validated baseline:** validated PR head `1dad684ab7d195c098366f82d73f50ec1fe79f1e` (proposed for main)  
**CI evidence:** GitHub Actions run `44713908209` for the validated PR head.  
**Nature of review:** repository-level automated validation and targeted source review. This is not an independent penetration test or a certification.

## Executive result

The required automated test and security jobs passed on the validated source tree. The repository is **not yet a clean, production-certified release**: the advisory Ruff job still reports legacy findings, coverage is 83%, and Windows/live-provider runtime behavior has not been verified in this environment.

## Automated validation

| Check | Result |
|---|---|
| Python 3.12 test suite | **489 passed** |
| Python 3.13 test suite | **488 passed** |
| Coverage | **83%** on both Python versions |
| Python compilation | Passed (`python -m compileall -q .`) |
| Dependency audit | `pip-audit`: no known vulnerabilities found |
| Bandit | **0 medium / 0 high** severity findings; 827 low-severity findings in the scan summary |
| SQLite resource lifecycle | The 23 Python 3.13 `ResourceWarning: unclosed database` warnings seen before the fix are no longer present |
| Ruff | Advisory job still fails with **103 findings**: 74 I001, 18 F401, 6 F841, 4 C408 and 1 S110 |

Ruff is configured as advisory in CI, so its failure does not fail the overall workflow. This must not be described as a clean lint result. The findings are repository-wide technical debt and should be addressed incrementally, starting with unused imports/locals and exception handling, then import formatting.

## Security controls reviewed

- Default application mode is loopback-only; LAN mode is explicit and requires a token.
- Host/origin checks, constant-time token comparison, bounded request handling and restrictive response headers are present.
- User-supplied UDF URLs are checked against configured allowlists; upstream redirects are disabled.
- CSV ingestion and research payload sizes are bounded.
- SQLite files and sidecars receive restrictive POSIX permissions; symlink paths are rejected.
- MCP starts without a shell, uses a reduced child environment and does not inherit application provider secrets or the LAN token.
- MCP integration is read-only and does not expose order placement.
- Holdout evaluation is serialized and one-time at the application layer.
- Dependency scanning found no known vulnerable dependency versions in the current requirements set.

See [SECURITY.md](SECURITY.md) for the detailed threat model, operational guidance and remaining security limitations.

## Important limitations and residual risk

1. **No TLS in the built-in server.** LAN mode uses plain HTTP. Do not expose it directly to the public Internet; use a trusted network and an HTTPS reverse proxy with authentication and network controls.
2. **Shared LAN token.** There are no per-user roles or server-side session revocation. Loopback clients are trusted by design.
3. **No independent DAST or penetration test.** Bandit and pip-audit are automated checks, not proof that the application is vulnerability-free.
4. **No Windows runtime certification in this audit.** CI runs on Ubuntu. A local Windows 11 smoke test is still required.
5. **No live exchange/provider certification.** Network reachability, provider rate limits, symbol coverage, data entitlements and current upstream payload behavior must be tested from the deployment environment.
6. **No profitability claim.** Backtests are bar-level approximations. They do not fully model order-book depth, queue position, partial fills, market impact, exchange liquidation or all historical funding mechanics.
7. **Coverage gap.** 83% line coverage leaves 17% unexecuted by the current suite; high-risk orchestration, failure and platform-specific paths need more targeted tests.
8. **Static-analysis debt.** The 103 Ruff findings remain open. The advisory configuration means they are visible but non-blocking.

## Project completeness

The current mainline is ChartAnalyzer V7.2.0 plus the merged CMI-01/CMI-02 evidence, source, entity and claim contracts. The later CMI stages are roadmap work, not completed functionality:

- CMI-03: Fundamental and Macro Intelligence
- CMI-04: News/research ingestion and source-linked claim extraction
- CMI-05: Human sentiment and narrative lifecycle
- CMI-06: Knowledge graph and contradiction engine
- CMI-07: Event impact and cross-asset hypotheses
- CMI-08: Scenario synthesis and forecast evaluation

The separate `feature/news-service` branch is not part of main and must not be treated as an integrated or validated production capability. Its news ingestion should be reconsidered as part of CMI-04, using the canonical evidence/source contracts and prompt-injection safeguards.

## Required follow-up before a production release

1. Eliminate the Ruff baseline and make linting of changed files blocking.
2. Increase coverage of security-sensitive and data-integrity paths, with particular attention to exception paths and Windows behavior.
3. Run the full test and smoke-test sequence on Windows 11 with the intended Python version.
4. Validate each live data adapter against its provider, including stale data, rate limits, malformed payloads and symbol/market identity.
5. Complete CMI-03 through CMI-08 with point-in-time, provenance and out-of-sample acceptance tests.
6. Collect a genuinely independent, sealed future holdout before making any predictive-performance claim.

**Conclusion:** the current code passes the automated test, dependency and medium/high Bandit gates on the recorded CI baseline. It is suitable for continued controlled development and research validation, but the findings and limitations above must be closed or explicitly accepted before production deployment.
