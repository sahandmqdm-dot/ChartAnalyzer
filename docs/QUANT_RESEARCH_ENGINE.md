# Quant Research Engine Architecture

## Data flow

1. `load_cached` obtains the selected source window.
2. `data_integrity.audit_ohlcv` rejects invalid OHLCV before research.
3. `research_provenance` fingerprints exact normalized candles and canonical execution configuration.
4. `walk_forward_backtest` evaluates the existing causal signal in sequential OOS folds.
5. `research_rule_engine` compiles pre-registered, safe declarative Long/Flat rules from causal features. `research_statistics` estimates block-bootstrap uncertainty, matched random-entry and entry-timing permutation controls.
6. `research_registry` stores the hypothesis and immutable experiment lineage; Benjamini-Hochberg adjusts the registered p-value family.
7. `strategy_validation` applies the fail-closed evidence gate.
8. `holdout_vault` prevents ordinary research endpoints from consuming a sealed final sample and allows one exact-config evaluation.

## Canonical execution identity

The live gate and OOS evidence use `_research_execution_config(tf, symbol, None, C)`. Report-side result fields are not allowed to override this fingerprint. The canonical identity includes indicator parameters, threshold, market type, fees, spread/slippage, market impact, execution delay, stops/targets, position sizing, leverage, funding and restricted hours.

## Statistical scope

- The trade-level moving-block bootstrap uses ordered OOS net trade returns.
- The one-sided test is a centered circular moving-block bootstrap against zero mean.
- FDR is applied across all completed registered experiments in the `chartanalyzer_frozen_signal` family with a valid p-value.
- Entry signal permutation reorders signal blocks against chronological forward open-to-open returns and charges turnover costs. It intentionally does not model ATR stops/targets, so it is a diagnostic control, not an acceptance test by itself.
- Robustness helpers return `INSUFFICIENT_EVIDENCE` when their minimum sample requirements are not met.

## Holdout lifecycle

1. Preview a candidate dataset using `/api/research-lab/fingerprint`.
2. Ensure the sample is genuinely untouched and begins after prior registered research.
3. Seal its `dataset_id`, `config_hash`, market identity and timestamps through `/api/research-lab/holdout/seal`.
4. Ordinary `/api/backtest` and `/api/walk-forward` requests overlapping that time range are blocked.
5. `/api/research-lab/holdout/evaluate` checks the exact dataset and strategy fingerprint and consumes the single-use claim before computing results.
6. Never tune against the returned holdout result. A changed strategy requires a new future holdout.

The vault is a local logical control, not a cryptographic boundary against a user with filesystem access.

## Non-goals

- No automatic parameter search.
- No autonomous strategy promotion.
- No order placement or exchange write access.
- No claim that current Ramzinex history is sufficient for final statistical acceptance.
- No use of Monte Carlo path frequencies as calibrated real-world probabilities.

## Dashboard/API access

- `POST /api/research-lab/run-hypothesis` runs a pre-registered declarative rule through the same cost-aware Walk-Forward engine. A custom hypothesis remains research-only and is not connected to the live gate.
- `GET /api/research-lab/random-entry` runs a matched random-entry baseline.
- `GET /api/research-lab/split-preview?n=...` previews chronological 60/20/20 boundaries.
- `POST /api/research-lab/robustness` summarizes only caller-supplied pre-registered OOS rows; it does not validate their provenance automatically.
- `POST /api/research-lab/hypotheses` registers an immutable hypothesis specification.
- `GET /api/research-lab/fingerprint` returns exact dataset/config fingerprints for holdout sealing.
- `POST /api/research-lab/holdout/seal` seals the final sample.
- `POST /api/research-lab/holdout/evaluate` consumes the one-time final evaluation.

The existing dashboard's Walk-Forward button remains the main frozen-signal OOS workflow. Its run is automatically recorded in the Registry.

## Declarative hypothesis example

Register the rule first with `POST /api/research-lab/hypotheses`, then execute the same specification with `POST /api/research-lab/run-hypothesis`:

```json
{
  "hypothesis_id": "HYP-ADX-PULLBACK-001",
  "name": "ADX trend pullback",
  "source": "ramzinex",
  "symbol": "USDTIRR",
  "tf": "1h",
  "rule_spec": {
    "entry": {"all": [
      {"feature": "adx", "op": ">=", "value": 20},
      {"feature": "group_trend", "op": ">=", "value": 0.2}
    ]},
    "exit": {"any": [
      {"feature": "score", "op": "<", "value": 0},
      {"feature": "group_trend", "op": "<", "value": 0}
    ]},
    "max_holding_bars": 24
  }
}
```

Supported features depend on the configured indicator groups. Unknown feature names and arbitrary code expressions are rejected. The rule is evaluated at candle close and executed at the next open plus configured extra delay.
