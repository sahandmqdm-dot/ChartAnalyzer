# Decision Quality and Abstention Audit

## Purpose
A useful decision system must be evaluated on both actions and abstentions. This
module compares a frozen ACT/NO_TRADE policy with a forced-action
counterfactual over the same resolved opportunities.

## Input contract
Each record requires:
- unique decision ID;
- instrument, market type, timeframe, and horizon;
- timezone-aware decision, horizon-end, and resolution timestamps;
- ACT or NO_TRADE action and a predeclared reason code;
- resolved hypothetical net account return if one unit-notional trade had been
  taken, including the modeled fees, spread, slippage, funding, and other costs.

Every opportunity must have a counterfactual outcome, including NO_TRADE.
Without outcomes for abstained opportunities, the system cannot measure either
avoided losses or missed gains.

Only one homogeneous cohort is evaluated per call. Overlapping horizons are
rejected because treating them as independent unit-notional opportunities
would double-count exposure and create false sample size.

## Metrics
- action coverage and abstention rate;
- policy return under the supplied unit-notional, non-overlapping convention;
- forced-action counterfactual return;
- avoided loss count and magnitude;
- missed positive opportunity count and magnitude;
- incremental descriptive value versus forced action;
- reason-code frequencies.

A positive incremental value is not proof that the policy caused an improvement.
It is a paired descriptive result conditional on the supplied counterfactual
returns and the declared evaluation convention.

## Interpretation guardrails
The result is always DESCRIPTIVE_ONLY. It is not a deployment gate, a
profitability claim, a causal estimate, or a portfolio backtest. It does not
model capital compounding, cross-asset interactions, capacity, market impact,
or execution uncertainty not already represented in the supplied net returns.

Reason codes and decision thresholds must be frozen before a prospective
evaluation. Confirmatory evidence requires a new untouched prospective cohort,
sufficient sample size, dependence-aware uncertainty intervals, and comparison
against relevant baselines. Do not tune the policy using the confirmatory cohort.

## Example
```python
from decision_quality import evaluate_decision_quality

report = evaluate_decision_quality(resolved_decision_records)
print(report["abstention"])
```
