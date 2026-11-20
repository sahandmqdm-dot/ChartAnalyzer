# ChartAnalyzer Product Vision & Research Charter

**Product identity:** Evidence-Driven Multi-Asset Market Intelligence, Probabilistic Future Scenarios & Portfolio Decision Platform.

## 1. Mission
ChartAnalyzer is not a signal generator and must not optimize for the number of trades. Its purpose is to help a researcher or portfolio decision-maker understand what may happen next, how uncertain that assessment is, what evidence supports it, what could invalidate it, and whether taking risk is justified at all.

The system must be able to conclude:
- **ACT / PAPER**: a research-qualified opportunity exists in simulation.
- **WAIT / MONITOR**: evidence is incomplete or the opportunity is not yet mature.
- **NO_TRADE**: expected net economics, risk, data quality, or evidence do not justify exposure.
- **NOT_VALIDATED**: a model or strategy has not passed the required independent validation.
- **DATA_INVALID**: market observations are stale, incomplete, inconsistent, or unauditable.

No live order placement is in scope. All execution development must remain read-only, replay, simulation, or paper trading unless a future explicit product decision changes that constraint.

## 2. Multi-asset scope
The platform uses a shared research contract with asset-specific adapters:
1. **Crypto Spot**: 24/7 markets, venue-specific symbols, quote currencies, fees, depth, trades, and exchange outages.
2. **Crypto Perpetuals and Futures**: contract specifications, mark/index prices, funding, open interest, liquidation events where available, leverage tiers, maintenance margin, and exchange-specific liquidation rules.
3. **US listed equities and ETFs**: NYSE/Nasdaq listings, regular and extended sessions, holidays, early closes, splits, dividends, symbol changes, corporate actions, borrow/short availability where licensed, and survivorship-aware universes.
4. **Portfolio intelligence**: cross-asset exposure, concentration, correlation instability, liquidity, drawdown, scenario loss, capital allocation research, and portfolio-level risk budgets.

A common instrument identity must never erase venue, contract, currency, session, or adjustment semantics. Spot, perpetual, dated futures, equities, and ETFs must not be silently mixed.

## 3. Five intelligence planes
### A. Market Data & Provenance
Raw immutable observations; provider and venue lineage; exchange event time and ingestion time; sequence numbers; gap detection; clock quality; schema version; content hashes; replay; and explicit data-quality states.

### B. Market Structure & Microstructure
Multi-timeframe price structure, volatility, liquidity, spread, depth imbalance, trade flow, funding/OI context, session effects, market impact, and anomaly hypotheses. A single metric must never be promoted into a definitive manipulation or intent claim.

### C. Hypothesis & Forecast Research
Frozen hypotheses, point-in-time features, multi-horizon return distributions, quantiles, scenario trees, regime-conditioned forecasts, forecast uncertainty decomposition, and timestamp-matched baselines.

### D. Statistical Evidence Governance
Purged/embargoed walk-forward evaluation, prospective holdouts, immutable experiment lineage, complete trial accounting, multiplicity control, dependence-aware uncertainty, calibration, drift monitoring, and reproducible evidence.

### E. Decision & Portfolio Intelligence
Net-of-cost expected utility, downside and tail risk, exposure constraints, opportunity cost, abstention quality, invalidation conditions, and portfolio interactions. The system should explain why it declines a trade, not just why it accepts one.

## 4. Forecast contract
Every forecast must be an auditable object with:
- instrument, venue, market type, timeframe, horizon, and forecast issuance time;
- exact data cutoff and the latest fully closed observation;
- model, feature, configuration, and code fingerprints;
- point forecast and predictive distribution (quantiles and/or scenarios);
- uncertainty decomposition: aleatoric, epistemic/model, data-quality, and regime uncertainty where estimable;
- baseline forecasts generated from the same information cutoff;
- explicit calibration status, sample count, and applicable regime;
- target time and resolution rule;
- no claim of calibrated probability until sufficient prospective resolved observations support it.

A directional score alone is not a forecast product.

## 5. Decision-quality contract
Decision quality is evaluated separately from forecast accuracy. The system must measure:
- net expectancy and distribution of outcomes after realistic costs;
- calibration and sharpness of probability forecasts;
- coverage and usefulness of prediction intervals;
- realized loss conditional on confidence and regime;
- performance of abstention (NO_TRADE) versus forced-action baselines;
- opportunity cost and missed-opportunity analysis;
- tail loss, drawdown, turnover, capacity, liquidity and concentration;
- robustness to cost, latency, spread, slippage, funding, and market impact stress.

No-trade decisions must be logged with reason codes so abstention itself can be evaluated prospectively.

## 6. Research integrity rules
- Point-in-time only: features at time t may use information available by the declared cutoff, never later revisions or future candles.
- Every experiment, including failed, abandoned, and exploratory trials, is registered before outcome inspection where practical.
- Research families and hypothesis relationships are defined before multiple-testing correction.
- Holdout data is sealed, access-audited, snapshot-hashed, and single-use for confirmatory evaluation.
- Walk-forward folds preserve temporal order and explicitly represent excluded embargo intervals.
- Portfolio returns are timestamped and period-aligned; gaps are never compressed into adjacent observations.
- ESS, bootstrap, DSR, PBO, and confidence intervals are diagnostics with declared assumptions, not magic approval scores.
- Statistical power and minimum effective sample sizes are justified by simulation, not selected to make a model pass.
- Evidence is bound to exact code, data, configuration, venue, instrument, and execution assumptions.
- Missing, stale, unsigned, mismatched, or insufficient evidence fails closed.

## 7. Execution realism ladder
Research results must disclose the level actually tested:
1. Candle-close signal / next-open approximation.
2. OHLC intrabar path approximation with conservative ambiguity handling.
3. Spread, fees, slippage, market impact, funding and borrow costs.
4. Venue-specific tick/lot/notional rules and margin tiers.
5. Order-book replay and latency-aware simulated fills.
6. Paper trading against live market data.
7. Independent prospective evidence.

A result at one level must not be described as validated at a higher level. Live order execution is excluded from the current product scope.

## 8. Release gates
**Research-ready** means reproducible tests and explicit assumptions.

**Validation-ready** means causal PIT features, clean time splits, baseline comparisons, complete trial lineage, and independent holdout controls.

**Paper-ready** means stable read-only market ingestion, execution-aware simulation, operational monitoring, and prospective paper evidence.

**Production research platform** means multi-asset data contracts, robust recovery, calibrated forecasts, portfolio-level risk, independent evidence, observability, and documented operational recovery.

A green CI pipeline proves code checks passed; it does not prove market edge, calibration, data independence, or profitability.

## 9. Explicit non-goals
- No promise of profitable signals.
- No automatic parameter fitting on the final holdout.
- No inference of trader intent or spoofing from one observable.
- No silent substitution of synthetic or stale data for real venue observations.
- No live order placement.
- No promotion of uncalibrated model confidence into real-world probability.

## 10. Near-term engineering sequence
1. Timestamped, bar-indexed equity events in the backtest engine.
2. Fold-aware OOS equity segments with explicit embargo metadata.
3. Per-fold time-series ESS diagnostics and robust aggregation research; no cross-gap compression.
4. Point-in-time audit of signal and feature generation.
5. Research-family governance and complete trial registration.
6. Holdout snapshot/freeze/access audit and crash-safe atomic claims.
7. DSR/PBO/ESS evidence integration only after independent tests and power studies.
8. Shared multi-asset data contracts for Crypto Spot/Futures and US equities/ETFs.
9. Portfolio-level risk and abstention-quality evaluation.
10. Prospective paper research across instruments and timeframes.

The product advances only when its evidence quality advances with its feature set.
