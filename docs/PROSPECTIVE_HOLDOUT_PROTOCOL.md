# Prospective Future Holdout Protocol

## Purpose

This protocol measures whether a frozen ChartAnalyzer forecast process performs on market observations that did not exist when the forecasts were issued. It is distinct from a historical backtest, a historical 60/20/20 split, or a conventional walk-forward replay.

A prospective holdout is collected forward in wall-clock time:

1. Freeze the exact model, code release, features, data policy, calibration, and execution assumptions.
2. Register a collection cohort before its start time.
3. Persist each forecast and its point-in-time input fingerprint before its target horizon.
4. Wait until the target horizon has elapsed and the outcome data is available.
5. Append the realized outcome without changing the original forecast.
6. Keep the score report hidden until the collection window has closed and every forecast has resolved.
7. Evaluate once. Never tune, select, or promote a model using this final sample.

## Historical OOS versus prospective holdout

| Method | What it proves | What it cannot prove |
|---|---|---|
| Chronological train/validation/test split | Correct temporal partition of an existing historical dataset | That the final segment was never inspected or used during prior development |
| Purged/embargoed walk-forward | Robustness across sequential historical test windows with label-overlap controls | Independence from historical model selection or data revisions |
| Prospective future holdout | Performance on observations collected after the frozen system was registered | Permanent future profitability or immunity to market-regime change |

The existing `holdout_vault.py` remains a one-time historical dataset guard. It does not itself create a genuinely future sample. The new `prospective_holdout.py` is the append-only forward-collection primitive.

## Registration contract

A cohort is registered before `collection_start` and fixes:

- Instrument and market type.
- Timeframe and forecast horizon in bars.
- Model ID and model version.
- SHA-256 configuration fingerprint.
- Future collection start/end timestamps.
- Minimum forecast count (default operational target: 200).

The configuration fingerprint supplied by the caller must cover the immutable release commit, model artifact or model version, feature/indicator pipeline, preprocessing, calibration parameters, strategy thresholds, market-data policy, and cost assumptions. A configuration change requires a new ledger and a new future collection window.

## Forecast issuance contract

Each `ForecastRecord` must have:

- A timezone-aware `issued_at` and `target_at`.
- `data_cutoff_at <= issued_at`.
- Exact instrument, market type, timeframe and horizon.
- Frozen model ID/version and dataset fingerprint.
- Quantile distribution and optional directional probability.
- A record-write timestamp at or after issuance and strictly before `target_at`.

The ledger checks that `target_at = issued_at + horizon_bars × timeframe`. Forecast horizons in one cohort may not overlap. This deliberately reduces sample count to avoid pretending that highly overlapping forecasts are independent observations.

A forecast is immutable after insertion. Duplicate forecast IDs are rejected.

## Outcome resolution contract

An outcome is accepted only when:

- The forecast exists in the same ledger.
- The target horizon has elapsed.
- The outcome data's availability timestamp is at or after `target_at` and no later than resolution.
- The realized log return is finite.
- The outcome source and exact outcome dataset fingerprint are recorded.

Outcomes are append-only and one-to-one with forecasts. Missing, stale, malformed or unavailable outcomes are not silently dropped. The final evaluation remains blocked until every registered forecast has a valid outcome.

The input provider and outcome provider may be different where an equivalent instrument and market definition exist. If the same provider must be used, the sample is still prospective in time, but it must not be described as independently sourced. Price basis, contract type, quote currency, and candle boundary must match exactly.

## Non-overlap and sample size

The ledger requires the next forecast in a cohort to be issued no earlier than the previous forecast's target. For a 4-bar horizon on 1-hour candles, this means at most one forecast every 4 hours. Two hundred resolved forecasts therefore require at least 800 hours of collection, plus any time needed for the final outcomes to mature.

Two hundred is a minimum gate for a first evaluation, not a guarantee of statistical power. Reliability bins and regime segments can remain sample-insufficient. Longer horizons require proportionally longer collection. Different instruments, market types, timeframes and horizons are evaluated separately and must not be pooled to manufacture sample size.

## Evaluation metrics

The ledger reuses `forecast_validation.evaluate_forecasts` and reports:

- Median forecast MAE and RMSE for the median return estimate.
- Quantile pinball loss for q10, q50 and q90.
- Coverage and width of the central 80% interval.
- Directional Brier score for forecasts that supplied P(return > 0).
- Reliability/calibration table, including sample-size flags.
- Separate cohorts by instrument, market type, timeframe and horizon.

The next evaluation layer must compare the candidate against pre-registered, timestamp-matched baselines (zero-return/random-walk, historical unconditional distribution, and a simple volatility-scaled baseline). Candidate-versus-baseline differences should use paired block-bootstrap confidence intervals because forecast outcomes can remain serially dependent even when target windows do not overlap.

No single metric establishes predictive edge. A model should not be considered validated merely because its point estimate is positive or one reliability bin looks favorable.

## Integrity and access controls

- SQLite transactions use `BEGIN IMMEDIATE` to serialize writers.
- Database records are append-only through SQLite update/delete triggers.
- Each event is linked through a SHA-256 hash chain.
- Integrity verification checks both the event chain and each persisted record payload.
- Forecast counts and collection status may be inspected while scores remain hidden.
- The final report is written once and cannot be overwritten through the ledger API.
- A second evaluation attempt is rejected.

This is tamper-evident operational control, not a cryptographic boundary against an administrator who can replace the database, remove triggers, and recompute the chain. A production deployment should run the collector under a restricted service identity and replicate the ledger and daily hashes to immutable/WORM storage controlled separately from the model operator.

## Current implementation boundary

`prospective_holdout.py` currently provides the tested ledger and evaluation primitive. It is not yet wired to the dashboard, live forecast generation, or an automatic market-data resolver. No future data has been collected and no prospective performance result exists yet. The API/UI integration and provider-specific outcome resolver are follow-on work; the holdout must remain locked until those are complete.

## Acceptance checklist before starting the clock

- [ ] Freeze and publish the release commit and full configuration fingerprint.
- [ ] Choose one precise instrument/market/timeframe/horizon cohort.
- [ ] Validate input and outcome source identity, candle boundaries, units and data licensing.
- [ ] Confirm clock synchronization and UTC timestamps.
- [ ] Store the ledger outside the source checkout with restricted write access.
- [ ] Register the cohort while its collection start is still in the future.
- [ ] Verify the first live forecast is persisted before its target time.
- [ ] Do not inspect aggregate performance or tune during collection.
- [ ] Resolve all forecasts only after their target horizons.
- [ ] Verify ledger integrity, then run the single final evaluation.
- [ ] Compare against pre-registered baselines and publish uncertainty, failures and missing-data counts.
