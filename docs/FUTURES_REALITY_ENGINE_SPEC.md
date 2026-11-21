# Futures Reality Engine: Contract-True Research Specification

**Status:** Design specification, not an implementation claim  
**Target:** Binance USD-M linear perpetuals first; other venues/contracts require separate adapters  
**Safety boundary:** Read-only capture, deterministic replay and paper simulation only. No live order placement.

## 1. The central design change

Do not build another Futures indicator. Build a **Contract-True Futures Reality Engine**.

The engine must answer five different questions without conflating them:

1. **What was observable?** (market-data truth)
2. **What was the contract state?** (contract, margin and funding truth)
3. **What could have filled?** (execution truth)
4. **What was the risk at that exact time?** (accounting and liquidation truth)
5. **Did the forecast add value after all of that?** (statistical truth)

A directional opinion is not a trade. A trade plan is not an executable order. A simulated fill is not a realized fill. A positive backtest is not prospective evidence.

## 2. Canonical event model

Every record carries:
- `venue`, `market_type`, `instrument_id`, `contract_type`, `margin_asset`, `settlement_asset`, `quote_asset`, `contract_multiplier`;
- `event_time`, `transaction_time` when supplied, `received_at`, `persisted_at`;
- `source_endpoint_or_stream`, `connection_session_id`, `sequence_or_trade_id`;
- `schema_version`, `raw_payload_hash`, `partition_id`;
- explicit `availability_status`, `freshness_ms`, `clock_quality`.

Do not overwrite exchange event time with local receive time. Do not infer missing exchange sequence numbers. Preserve the raw frame before parsing.

### Price planes
Keep distinct time series for:
- Last/Trade price;
- Best bid and ask;
- Mark price;
- Index price;
- Contract-specific trigger/reference price.

Every derived value records its price plane. A stop may trigger on mark or contract price depending on order type/exchange rules; the simulator must not assume last-price triggers universally.

## 3. Contract registry and temporal rules

Create immutable, versioned contract snapshots with effective intervals:
- symbol and exchange instrument ID;
- linear/inverse/quanto classification;
- base, quote, margin and settlement assets;
- contract size/multiplier and quantity unit;
- tick size, lot step, min/max quantity, min notional;
- price and quantity precision;
- leverage brackets and maintenance margin formula;
- maintenance amount, liquidation fee and risk tiers;
- supported order types and trigger-price semantics;
- funding interval and settlement convention;
- status (trading, reduce-only, halted, delisted).

Each research run binds to one contract snapshot hash. If a required field is absent or stale, return `CONTRACT_UNVERIFIED`, never silently use a generic default.

## 4. Futures event ledger

Normalize the following into separate append-only streams:
- trades / aggregate trades;
- depth snapshot and depth diffs;
- mark price and index price;
- funding rate observations and actual funding settlement events;
- open-interest observations with native units;
- liquidation/force-order messages with their source limitations;
- exchange status and contract metadata changes.

For each stream calculate coverage, delay distribution, duplicate rate, gap count, reconnect count, and event-time versus receive-time skew. A feed that is connected but incomplete is not healthy.

Liquidation feeds must be described according to the exchange's publication semantics and sampling limits. Never label the observed feed as the complete universe of liquidations unless the venue contract guarantees that.

## 5. Contract-accurate account simulation

Represent account state at every event:
- wallet balance by asset;
- isolated/cross mode;
- position quantity and side;
- entry price and mark price;
- realized and unrealized PnL;
- accrued/settled funding;
- initial and maintenance margin;
- open orders and reserved margin;
- leverage bracket selected at that notional;
- fee tier and fee asset.

For isolated linear contracts, liquidation is computed from the venue's current published bracket/rules and exact fee conventions. For cross margin, all positions and collateral must be modeled jointly. If account-level inputs are absent, the engine may produce a bounded educational estimate, but it must not call it exchange-accurate liquidation.

Funding is booked at each settlement timestamp using the applicable rate and position notional at the exchange-defined snapshot rule. A current funding rate must never be projected across the whole holding period as if known.

## 6. Execution Reality Ladder

Expose an explicit tested level for each result:

- **E0 Candle approximation:** OHLCV only; no execution claim.
- **E1 Quote-aware:** timestamped bid/ask and fees; simple marketable fill approximation.
- **E2 L2 sweep replay:** valid synchronized depth, sequence continuity, bounded latency and depth consumption.
- **E3 Queue-aware passive replay:** queue position, cancellations, trades at price, priority, partial fills and adverse selection assumptions.
- **E4 Paper shadow:** decisions and hypothetical orders recorded against live market data, no exchange submission.
- **E5 Prospective validation:** frozen model and execution assumptions, independent future outcomes.

A result cannot inherit a higher level from another module. E2 does not imply E3. A replay estimate is not a realized execution.

### Conservative fill rules
- Marketable orders consume visible depth and report residual quantity as unfilled.
- Never fill beyond displayed/eligible liquidity without an explicitly modeled impact extension.
- Limit orders do not fill merely because candle high/low touches the limit.
- Queue assumptions must be pessimistic and reported as assumptions.
- Latency shifts the information and book state; do not convert latency into arbitrary bps.
- Reject fills if book validity, sequence continuity or timestamp alignment fails.

## 7. Futures-specific forecast and decision contract

Every forecast binds to:
- exact contract snapshot;
- point-in-time OHLCV and raw event manifest;
- trade-flow, L2, funding, OI, mark/index and liquidation feature versions;
- data cutoff and source-event watermark;
- model/feature/config/code hashes;
- forecast horizon and target resolver;
- timestamp-matched baselines;
- calibration and effective sample-size state.

The candidate model must be compared against:
1. zero-return / no-change baseline;
2. historical unconditional return distribution;
3. volatility-scaled baseline;
4. simple price-only model;
5. price + derivatives context model;
6. price + trade flow + L2 model.

Use paired, dependence-aware OOS comparisons. Ablation studies must be pre-registered: price-only vs price+funding/OI vs price+flow/L2. This tells us whether sophisticated data adds incremental information instead of merely decorating a chart.

## 8. Innovative research layer: Market State Transition Graph

Represent market state as a causal graph rather than a single bullish/bearish score.

Nodes (each with timestamp, confidence/quality and provenance):
- volatility/liquidity regime;
- aggressor trade-flow imbalance and CVD;
- depth imbalance and depth replenishment/depletion;
- spread and executable depth;
- mark-index basis;
- funding level and change;
- OI change in native and quote-notional units;
- liquidation bursts and their observed coverage;
- price response to aggressive flow;
- realized volatility and jump risk.

Edges are hypotheses, not facts. Examples:
- aggressive buy flow + rising price + stable/decreasing OI;
- aggressive buy flow + rising OI + rising premium;
- liquidation burst + depth vacuum + abnormal spread;
- large flow with little price response (possible absorption hypothesis).

The system should label these as **observed pattern hypotheses**, not infer intent, manipulation or certainty. Evaluate each pattern against future outcomes on a preregistered horizon and compare it with price-only baselines.

Key novel metric: **Flow-to-Price Efficiency (FPE)** = robust price response per unit of signed aggressive notional, normalized by volatility and available depth. Track it by regime and time horizon. It is a research feature, not a standalone signal. Test whether changes in FPE predict continuation, exhaustion or neither.

Another key metric: **Liquidity Recovery Time (LRT)** = time for spread and depth to recover after a detected flow/liquidation shock. Treat missing observations and feed gaps as censored/invalid, not zero recovery. Test its incremental value for short-horizon risk and execution cost.

## 9. Validation protocol

- Register a hypothesis family and all candidate variants before inspecting outcomes.
- Freeze data, features, code, model and execution assumptions.
- Use purged/embargoed historical folds for development, then one prospective cohort.
- Do not use overlapping labels as independent samples.
- Estimate statistical power and minimum detectable effect before choosing cohort length.
- Use paired block bootstrap or other dependence-aware intervals.
- Report all attempted variants and apply multiplicity control.
- Evaluate forecast skill, trade economics and decision/abstention quality separately.
- Split results by volatility, liquidity, funding regime and event type only when sample sizes support it.
- No promotion on one metric; no automatic strategy activation.

## 10. Acceptance gates

A Futures candidate remains `NOT_VALIDATED` unless all applicable gates pass:

1. Contract identity and rules are fresh and hash-bound.
2. Price planes and trigger semantics are explicit.
3. Raw event coverage and sequence integrity pass.
4. PIT feature adapter is tested against replay and leakage fixtures.
5. Funding and margin accounting reconcile to deterministic reference cases.
6. Execution replay passes independent reference calculations.
7. Fees, impact and latency assumptions have source and sensitivity analysis.
8. Candidate beats timestamp-matched baselines net of costs with uncertainty reported.
9. Prospective evidence is independent and adequately powered.
10. Decision-quality and abstention metrics are acceptable.

Failure of any critical gate produces `DATA_INVALID`, `INSUFFICIENT_EVIDENCE`, `UNVERIFIED` or `NO_TRADE`, not a weakened green status.

## 11. Implementation sequence

**Slice A:** contract identity, price-plane schemas, temporal contract registry and UI truth labels.  
**Slice B:** USD-M PIT adapter binding raw manifest -> features -> forecast ledger.  
**Slice C:** funding settlement and tier-aware isolated margin reference engine.  
**Slice D:** E1/E2 execution replay with independent reference implementation.  
**Slice E:** E3 queue-aware replay research.  
**Slice F:** Market State Transition Graph, FPE and LRT feature research.  
**Slice G:** frozen Futures prospective cohort, only after all preceding gates.

No 42-day clock starts as part of this specification.
