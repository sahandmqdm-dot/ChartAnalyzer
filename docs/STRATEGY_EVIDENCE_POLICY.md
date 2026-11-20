# Strategy Evidence, Decision Classes and Lifecycle Policy

## Objective

ChartAnalyzer must not assume that a strategy is profitable or that every market regime contains a tradable edge. The research system is successful when it correctly distinguishes evidence of an edge, evidence against an edge, insufficient evidence, and invalid data.

The current fail-closed numerical gate remains unchanged: at least 200 executed OOS trades, at least 5 chronological folds, net profit factor >= 1.20, positive net expectancy, at least 70% positive folds, maximum OOS drawdown <= 15%, adjusted p-value <= 0.05, positive lower bound of the moving-block-bootstrap 95% interval, gap-free data, and positive expectancy under the configured 1.5x cost stress. These are conservative engineering gates, not universal statistical laws and not a promise that passing will be profitable in future.

## Decision classes

The strategy validation response now includes `decision_class` while preserving the existing `status` field for API compatibility.

| Decision class | Meaning | Required response |
|---|---|---|
| `DATA_INVALID` | Data integrity, gaps, or OOS cohort provenance failed | Block; repair data/provenance before interpreting performance |
| `INSUFFICIENT_EVIDENCE` | Minimum OOS trade/fold/sample requirements or essential inference fields are absent | Keep unverified; collect more independent evidence |
| `NO_EDGE` | Adequately sampled net expectancy is <= 0 or net PF is <= 1 | Do not trade this frozen strategy/configuration |
| `UNVERIFIED` | Evidence exists but does not satisfy all validation controls, including freshness, stability, cost stress or confidence bounds | Block; do not describe as profitable or validated |
| `PAPER_CANDIDATE` | All current validation gates pass | Eligible for separately controlled paper evaluation only; not a claim of future profitability |

Missing evidence is classified as `UNVERIFIED`. Existing `status` values (`MISSING`, `BLOCKED`, `ELIGIBLE`) are retained so existing API clients are not silently broken.

## Cost model requirements

Every result must disclose the exact fee schedule and execution assumptions used for that venue, instrument, account tier and order type. The model must separately represent maker/taker fees, spread, slippage, market impact, execution delay, funding/borrow where applicable, and rejected/unfilled limit orders. A limit order is not assumed to receive maker fees or a fill. If queue position and fill probability are not modeled, the report must label the result as an approximation and must not claim realistic execution.

The user-supplied USDT/IRR observation of approximately 0.5% round-trip cost and 0.23% average MFE is a specific observed scenario, not a universal default. Such a cost/price-move mismatch is an economic NO_TRADE condition for that tested setup. The application must use verified current venue/account fee inputs rather than hard-code this observation across markets.

## Strategy lifecycle and retirement

A single failed backtest, stale evidence record, invalid capture, or underpowered window must not retire a strategy. The new `evaluate_strategy_lifecycle` helper only returns `RETIRE_CANDIDATE` after at least two consecutive, non-overlapping, sufficiently sampled frozen OOS windows each show:
- net expectancy below zero;
- net profit factor below 1;
- the upper bound of the 95% block-bootstrap interval below zero;
- valid data and passing cohort integrity;
- no parameter optimization.

The helper is advisory and does not mutate the registry or disable a strategy. Any operational retirement workflow must record the decision, exact evidence-window identities, approver, timestamp and reason. A changed strategy/configuration is a new research identity and must earn new evidence.

## Prospective evaluation

The 42-day prospective collection is not an automatic profitability test and must not be tuned during collection. Its purpose is to estimate future forecast and decision quality against pre-registered baselines on future observations. Strategy-level trade evidence and forecast-distribution evidence are distinct outcomes and must not be conflated. A successful collector, passing CI, or intact hash chain proves neither market edge nor positive returns.

The final report must include all registered hypotheses/trials, net-of-cost metrics, confidence intervals, drawdown, sample/effective sample size, missing-data and capture-loss counts, cost sensitivity, baseline deltas and limitations. A negative result is a valid research result. No strategy is promoted solely because it is the least bad candidate.

## Safety boundary

This policy does not enable live order placement, tune parameters, change signal weights, or start the 42-day clock. `PAPER_CANDIDATE` is not production authorization.
