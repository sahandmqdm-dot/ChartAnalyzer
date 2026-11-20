# ChartAnalyzer Architecture and Research Boundaries

## Mission

ChartAnalyzer is a local quantitative market-analysis and research platform. Its purpose is to validate market data, compute causal indicators and market context, evaluate forecasts and strategy evidence, and block actionable decisions when required evidence is absent or weak.

A positive technical score is not, by itself, proof of statistical edge. `NO_TRADE` is an intentional outcome.

## Design principles

1. **Causal inputs:** features used for a decision must have been available at that decision time.
2. **Evidence before deployment:** in-sample results and indicator agreement do not authorize a trade.
3. **Net execution economics:** fees, spread, slippage, impact, delay, sizing and configured funding must be represented in the relevant evaluation.
4. **Uncertainty is explicit:** bootstrap intervals and directional forecasts remain diagnostics unless calibrated and validated.
5. **Reproducibility:** normalized OHLCV and canonical execution configuration are fingerprinted.
6. **Fail closed:** missing, stale, mismatched or insufficient OOS evidence blocks eligibility.
7. **No automatic promotion:** research outputs do not silently change the live signal or execution rules.

## Application flow

1. Load OHLCV from a configured source (Ramzinex, CCXT, UDF or CSV).
2. Normalize and audit timestamps and OHLCV shape using `data_integrity.py`; invalid data is blocked, not synthetically filled.
3. Compute indicators and causal market context.
4. Produce trend, regime, statistical-edge and forecast diagnostics.
5. Evaluate the decision using the configured risk and trade gates.
6. Optionally run the next-open backtest and sequential Walk-Forward evaluation.
7. Record provenance and OOS experiment evidence where enabled.
8. Apply `strategy_validation.py` before a strategy can be marked eligible.

## Core modules

| Module | Responsibility and boundary |
|---|---|
| `app.py` | HTTP API, dashboard, orchestration, configuration and request validation. |
| `data_integrity.py` | OHLCV integrity, timestamp spacing, gaps and stale-candle warnings. Does not repair or fill candles. |
| `trend_engine.py` | Market-structure and trend diagnostics. |
| `market_regime_intelligence.py` | Causal regime and transition diagnostics; research output does not automatically tune signals. |
| `statistical_edge.py` | Forward-return association and sample/effective-sample diagnostics. |
| `forecast_engine.py` | Distributional forecast diagnostics; probabilities require calibration before operational interpretation. |
| `backtest_engine.py` | Bar-level next-open simulator with configured transaction costs, optional ATR exits and synthetic futures exposure. |
| `walk_forward.py` | Chronological, non-overlapping OOS evaluation of the supplied frozen signal. |
| `research_provenance.py` | Dataset/config fingerprints, run identity and diagnostic cohort consistency. |
| `research_statistics.py` | Moving-block bootstrap, block permutation, matched random-entry control and Benjamini-Hochberg correction. |
| `research_protocol.py` | Chronological split, purged/embargoed split and feature-availability audit primitives. |
| `research_rule_engine.py` | Safe declarative Long/Flat research-rule compiler; no arbitrary Python expressions. |
| `research_registry.py` | Append-only hypothesis/experiment registry and registered-family FDR calculations. |
| `research_robustness.py` | Parameter-neighborhood, cross-symbol and regime stability summaries. |
| `entry_edge_research.py`, `exit_policy_research.py` | OOS trade-level entry and exit diagnostics; not automatic optimizers. |
| `holdout_vault.py` | Historical dataset holdout lifecycle guard; not a creator of future independent samples. |
| `prospective_holdout.py` | Append-only future forecast/outcome ledger, frozen cohort, non-overlapping horizons, hash-chain verification and one-time final evaluation. |
| `prospective_baselines.py` | Frozen zero-return, historical unconditional and EWMA-Gaussian forecast baselines with paired moving-block-bootstrap comparison. |
| `strategy_validation.py` | Fail-closed OOS evidence gate for a precise market/configuration identity. |
| `iran_crypto_market.py`, `iran_crypto_mcp.py` | Optional read-only Iranian exchange data integration through MCP. Not used to silently augment historical research samples. |
| `agent_core.py` | Decision support, trade gate and local prediction journal. |
| `margin_engine.py` | Exchange-neutral isolated-margin estimate; not an exchange liquidation oracle. |

## Quant Research Engine (QRE) data flow

1. Fingerprint the exact normalized OHLCV window and canonical strategy/execution settings.
2. Run the existing causal signal through sequential OOS Walk-Forward folds.
3. Register hypotheses before evaluating them; declarative rules are compiled from supported causal features.
4. Estimate dependence-aware uncertainty and compare against pre-defined random-entry and entry-timing controls.
5. Preserve completed and failed experiments in the registry and adjust the registered p-value family using FDR.
6. Keep the final holdout separate from ordinary research endpoints and permit only one exact-configuration evaluation.
7. Evaluate the latest matching evidence using the fail-closed strategy gate.

The detailed historical research protocol and API contracts are maintained in `docs/QUANT_RESEARCH_ENGINE.md`. The prospective future-sample protocol is specified in `docs/PROSPECTIVE_HOLDOUT_PROTOCOL.md`.

## Execution model and limitations

- The historical simulator observes the signal at candle close and normally fills at the next candle open, with optional additional execution delay.
- OHLC bars do not reveal the true intrabar path, order-book depth, queue position or actual market impact. When both ATR stop and target are touched in one bar, the simulator uses a conservative stop-first assumption.
- Futures exposure is synthetic. The historical backtest does not model exchange liquidation, margin calls or historical funding schedules; configured constant per-bar funding is only a simplified assumption.
- The buy-and-hold benchmark is synthetic and uses the configured cost model, not an exchange-native index.
- The entry-timing permutation and matched random-entry controls are diagnostics, not full stop/target/order-book execution simulations.
- Historical holdout sealing is a local logical control. The prospective ledger is append-only and tamper-evident, but a person with filesystem access can still replace its database and recompute its hash chain.
- LAN mode uses HTTP; do not expose it directly to the public internet. Use a trusted network, firewall and HTTPS reverse proxy where remote access is required.
- Optional MCP integration is read-only by design and remains disabled unless configured.

## Validation and release policy

GitHub Actions runs the Python test suite on Python 3.12 and 3.13, dependency auditing, Bandit scanning and advisory Ruff analysis. A green required CI run is evidence of software checks, not proof of trading profitability, live exchange compatibility or Windows deployment readiness.

Before a release is considered operationally ready, separately verify:
- Windows / Python 3.12 installation and runtime.
- Ramzinex read-only connectivity and data normalization.
- Historical dataset provenance, gap policy and symbol/timeframe alignment.
- OOS evidence on untouched chronological data for the exact strategy configuration.
- Execution costs, stress scenarios and exchange-specific futures mechanics.

No release should claim profitability without independently reproducible, sufficiently powered, net-of-cost OOS evidence.
