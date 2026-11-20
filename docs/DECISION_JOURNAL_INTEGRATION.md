# Prediction Journal to Decision Quality Integration

## New journal fields
New forecasts now persist the following frozen metadata in the existing
prediction journal's `extra` JSON payload:
- `decision_action`: ACT when the frozen gate is ALLOWED, otherwise NO_TRADE;
- `decision_reason_code`: first gate reason, or GATE_ALLOWED;
- `fee_roundtrip` and `spread_slippage_roundtrip`: the cost assumptions used
  by the gate at issuance;
- `market_type` and an explicit fixed-horizon counterfactual basis label.

Legacy rows without these fields remain readable by the original forecast
evaluation endpoint but are excluded from decision-quality scoring. No action
or cost assumptions are fabricated retrospectively.

## Resolution and counterfactual
The existing resolver records the close of the target candle only after that
candle is complete. The new endpoint derives a fixed-horizon long return:
`outcome / entry_price - 1 - frozen_round_trip_fee - frozen_round_trip_slippage`.

This is a directional, fixed-horizon counterfactual. It is **not** the
stop/target execution PnL, does not resolve intrabar stop-versus-target order,
and does not include market impact or funding unless represented by the stored
cost fields. Do not interpret it as realized trading performance.

## Overlap handling
Journal forecasts can be emitted on every bar, so their target horizons overlap.
For the single-unit diagnostic, resolved candidates are sorted by issue time and
the earliest non-overlapping opportunities are retained. Excluded overlapping
rows are counted in the response. This avoids double-counting overlapping
forecasts as independent unit-notional opportunities, at the cost of reducing
the sample.

## API
`GET /api/journal/decision-quality?symbol=ramzinex:BTCIRR&tf=1h&horizon=4`

The response contains:
- rows seen and metadata exclusions;
- overlap exclusions;
- descriptive ACT/NO_TRADE metrics from `decision_quality.py`;
- explicit limitations and the fixed-horizon return basis.

The endpoint is diagnostic only. It is not wired to strategy validation,
deployment eligibility, order execution, or any profitability claim.

## Rollout
Only forecasts logged after this change carry complete decision metadata.
Historical rows are intentionally not backfilled because their exact decision
reason and frozen cost assumptions cannot be reconstructed reliably.
