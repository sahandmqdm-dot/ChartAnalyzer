# ChartAnalyzer V6 Architecture

## Mission
Transform ChartAnalyzer from a technical-analysis scoring tool into a quantitative market intelligence and future-prediction platform that:
- Validates data integrity before analysis
- Detects market structure and regimes causally
- Evaluates statistical edge (and rejects signals without evidence)
- Produces multi-horizon distributional forecasts with uncertainty
- Tracks forecast accuracy in a persistent journal
- Applies realistic costs and risk gates before any trade decision
- Supports reproducible backtesting and strategy autopsy

## Design Principles
1. **Causal by construction** — no look-ahead; forming candles are never treated as closed history.
2. **Evidence over indicators** — technical agreement is not edge.
3. **Uncertainty is first-class** — every forecast carries quantiles and calibration status.
4. **NO_TRADE is a valid and preferred outcome** when evidence or economics fail.
5. **Reproducibility** — seeds, model versions, data cutoffs stored with every forecast.
6. **Mathematical consistency** — Gross R, Net R and Expected Value must reconcile.

## Module Map (V6)

| Module | Role |
|--------|------|
| `data_integrity.py` | OHLCV validation, gaps, staleness, shape checks. Blocks analysis on critical failure. |
| `trend_engine.py` | Market structure (HH/HL/LH/LL, BOS/CHoCH approximations, FVG, trendlines, fib). |
| `predictive_intelligence.py` | Regime classification, bootstrap cone (legacy + extended). |
| `statistical_edge.py` | Forward-return association, t-stats, effective sample size, evidence levels. |
| `forecast_engine.py` | Multi-horizon block-bootstrap forecasts, quantiles, experimental directionals, scenarios. |
| `backtest_engine.py` | Causal next-open long/short backtester, trade log, autopsy summary, cost-aware metrics. |
| `walk_forward.py` | Sequential non-overlapping out-of-sample evaluation of a frozen causal signal. |
| `margin_engine.py` | Exchange-neutral isolated-margin and liquidation-threshold estimate for linear Futures; separate from historical backtest execution. |
| `agent_core.py` | Trade gate (EV, net R, sample-size), prediction journal (SQLite), calibration helpers. |
| `app.py` | HTTP API + lightweight UI server, multi-timeframe orchestration, providers. |

## Data Flow (per request)
1. Load OHLCV (Ramzinex / CCXT / UDF / CSV)
2. `audit_ohlcv` → BLOCKED or PASS
3. Drop forming candle for historical analysis
4. Compute indicators + regime-weighted score (causal)
5. Structure analysis (trend_engine)
6. Statistical edge evaluation on the score series
7. Multi-horizon forecast (forecast_engine)
8. Risk / decision gate (agent_core.decide)
9. Optional journal write + later resolve
10. Optional backtest on the same causal signal
11. Optional frozen-signal Walk-Forward validation with isolated OOS folds
12. Optional standalone Futures isolated-margin estimate through `/api/margin`; it is not part of the historical trade simulator

## Forecasting Policy
- Primary distribution: moving-block bootstrap of demeaned log returns.
- Drift is added only when a t-test on recent returns supports it.
- Directional probabilities derived from bootstrap mass are labelled **EXPERIMENTAL_UNCALIBRATED** until the Forecast Journal has enough resolved outcomes for calibration.
- Monte Carlo path frequencies are never presented as real-world probabilities.

## Known Limitations (honest)
- Full SMC (order blocks, liquidity sweeps with institutional volume) is partial; structure is based on swing and FVG heuristics.
- Neural sequence models (LSTM/Transformer) are not included in this release; they require substantial data, GPU, and rigorous walk-forward harnesses that exceed current dependency and validation scope.
- Backtest remains bar-level next-open; true order-book simulation is not present.
- Probability calibration is journal-driven and starts empty for new installations.
- Short-side support in backtest is new and should be treated as experimental until more exhaustive tests pass.
- Futures margin calculator uses a linear isolated-position approximation; exchange tiers, Cross margin, mark-price mechanics and actual liquidation execution are not modelled.
- Historical Futures backtests do not yet simulate margin calls, liquidation events or historical funding schedules.

## Futures margin assumptions
`margin_engine.py` estimates initial margin as `entry_price * quantity / leverage`. It solves for the price at which isolated equity reaches a reserve based on `maintenance_margin_rate + liquidation_fee_rate`. The extra fee reserve is intentionally conservative and exchange-neutral. Quantity is in base units and all monetary outputs are in quote currency. It must not be treated as an exchange liquidation oracle.

## Testing
See `tests/`. Critical invariants: no look-ahead in scores, fee accounting, data integrity blocking, edge engine sample guards.
