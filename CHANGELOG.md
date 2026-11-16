# Changelog

## 7.2.0 - 2026-11-16
- Added Quant Research Engine: immutable hypothesis/experiment registry, dataset/config lineage and multiple-testing controls.
- Added circular moving-block bootstrap inference and block signal-permutation controls.
- Added execution-delay and market-impact cost modeling to backtests and passive benchmarks.
- Added chronological split, purged/embargoed CV and feature-availability audit primitives.
- Added parameter-neighborhood, regime and cross-symbol robustness research tools.
- Added a one-way final holdout seal with overlap protection and one-time exact-config evaluation.
- Integrated Research Lab dashboard/API and unified the canonical strategy fingerprint with the OOS deployment gate.
- Tightened deployment evidence floors: at least 200 OOS trades, PF >= 1.20, drawdown <= 15%, positive bootstrap lower bound and FDR q <= 0.05.
- No claim of profitability; deployment remains fail-closed until matching, fresh OOS evidence satisfies every gate.

## 7.1.0 - 2026-11-15
- Added optional read-only iran-crypto-mcp stdio client and schema-driven tool invocation.
- Added dashboard/API endpoints for price comparison, order books, candles and connection status.
- Added Windows setup script and Persian setup/release documentation.
- Kept live exchange snapshots isolated from historical backtest and OOS data.
- No signal, entry/exit, backtest or OOS gate logic changes.
- 184 pytest tests passed; upstream live test blocked by build-environment DNS/PyPI access.

## 7.0.0 - 2026-11-15
- Reproducible run and dataset fingerprints; explicit sample provenance.
- Shared full-window/OOS sample control and aligned first next-open fill.
- OOS cohort integrity audit and entry-edge attribution with moving-block bootstrap.
- Fail-closed deployment eligibility gate backed by fresh matching net OOS evidence.
- OHLCV integrity blocking before analysis, backtest, walk-forward and forecast.
- Fixed forensic candle timestamps and clarified report labels.
- No strategy score/entry parameter optimization or signal rule change.

## [6.9.4] - 2026-11-15

### Added
- Added paired Exit Policy Research for early time-exit counterfactuals on chronological Walk-Forward OOS trades.
- Reports counterfactual net expectancy, same-cohort actual expectancy, paired delta, win rate, profit factor, improved/worsened trade share, and deterministic bootstrap confidence intervals.
- Comparisons are restricted to trades where the proposed early exit precedes the actual exit; existing ATR barriers and modeled costs are retained by upstream reconstruction.

### Corrected
- Fixed Exit Path Intelligence aggregate MFE capture: aggregate mean/median now use only positive gross exits with positive MFE and expose the denominator. Losing trades with positive MFE remain separately counted; per-trade signed ratios remain available for audit.

### Guardrails
- No signal, entry, stop, target, or exit rule was changed. No parameter search or horizon selection is performed.
- Counterfactuals are paired trade-level diagnostics, not a portfolio backtest and not evidence of executable profitability.

### Validation
- 161 pytest tests passed; Python compilation, embedded dashboard JavaScript syntax, and synthetic Walk-Forward smoke tests passed.
- No raw OHLCV market dataset was included in the baseline archive, so no new real-market performance claim is made.

## [6.9.2] - Market Regime Intelligence
### Added
- Added causal trailing-window classification for Trending, Ranging, Volatile, Compression, and Transition states.
- Added transition event metadata (`transition_from`, `transition_to`) and diagnostic ADX, Efficiency Ratio, ATR percentile, and Bollinger-width percentile.
- Exposed the current regime state in the dashboard and attached signal-close regime labels to executed backtest trades.
- Added separate regime and transition cohort analysis for full-history backtests and chronological Walk-Forward OOS trades, preserving configured fees and execution costs.
### Unchanged
- Legacy ADX regime weighting remains the only regime input to the existing aggregate signal.
- No signal, indicator weight, threshold, entry/exit rule, or parameter was changed or optimized.
### Validation
- 154 pytest tests passed; causal prefix-invariance and future-perturbation tests added.
- Market-regime cohort results are research diagnostics, not proof of edge or permission for live trading.

## 6.5.0 - 2026-11-13

- Indicator and OHLCV integrity auditing; explicit metric semantics and unit display.
- Block trade plans when active indicators lack valid latest values or critical data integrity fails.
- Correct MFI edge-case handling for zero negative money flow.

## 6.4.0 - 2026-11-13

- Added descriptive Strategy Autopsy slices by position side, entry regime, signal-strength band, exit reason and holding duration.
- Captures aggregate and group indicator context on the signal candle immediately preceding the next-open fill, preserving causal timing.
- Reports net expectancy, win rate, profit factor, MFE/MAE, and best/worst trade examples by group.
- Renders autopsy results automatically after a dashboard backtest.
- Adds a Futures calculator warning when mark and entry prices differ by more than 30%, helping catch quote-unit or market mismatches.
- No parameter optimization or automatic strategy-rule changes are performed.

## 6.3.0 - 2026-11-13

- Added `margin_engine.py` for exchange-neutral isolated-margin and liquidation-threshold estimates for linear Futures positions.
- Supports Long and Short, leverage, base-asset quantity, additional isolated collateral, maintenance-margin rate and liquidation-fee reserve.
- Optional mark price reports unrealized PnL, estimated equity, reserve-adjusted margin ratio, signed liquidation distance and health state.
- Added `/api/margin` and a dashboard calculator for isolated Futures risk.
- Added configurable maintenance/liquidation rates, leverage ceiling and warning thresholds under `futures_margin`.
- Rejects Spot liquidation requests, unsupported Cross mode, invalid parameters and invalid explicit API values.
- Added deterministic formula, validation, health-state, API and dashboard regression tests.

**Scope:** this is a standalone estimate for a linear quote-settled isolated position. It does not replicate exchange-specific tiered maintenance margins, cross collateral, mark-price rules, liquidation engine, actual liquidation fees, historical funding, or margin-call execution. It is not wired into historical backtest liquidation simulation.

# ChartAnalyzer Changelog

## [6.9.4] - 2026-11-15

### Added
- Added paired Exit Policy Research for early time-exit counterfactuals on chronological Walk-Forward OOS trades.
- Reports counterfactual net expectancy, same-cohort actual expectancy, paired delta, win rate, profit factor, improved/worsened trade share, and deterministic bootstrap confidence intervals.
- Keeps each comparison restricted to trades where the proposed early exit precedes the actual exit; existing ATR barriers and modeled costs are retained by the upstream reconstruction.

### Corrected
- Fixed Exit Path Intelligence aggregate MFE capture: aggregate mean/median now use only positive gross exits with positive MFE and expose the denominator. Losing trades with positive MFE remain separately counted; per-trade signed ratios remain available for audit.

### Guardrails
- No signal, entry, stop, target, or exit rule was changed. No parameter search or horizon selection is performed.
- Counterfactuals are paired trade-level diagnostics, not a portfolio backtest and not evidence of executable profitability.

### Validation
- 161 pytest tests passed; Python compilation, embedded dashboard JavaScript syntax, and extracted-package tests passed.
- No raw OHLCV market dataset was included in the baseline archive, so no new real-market performance claim is made.

## 6.2.1 - 2026-11-13

- Exposed Walk-Forward through `/api/walk-forward` with bounded request parameters and config defaults.
- Added a dashboard action and fold-by-fold OOS performance table.
- Displays aggregate net return, expectancy, profit factor, win rate, drawdown, sample-size warning and validation assumptions.
- Added API routing, input-boundary and dashboard integration tests.

## 6.2.0 - 2026-11-13

- Added sequential, non-overlapping out-of-sample Walk-Forward evaluation for a frozen causal signal.
- Each fold uses only preceding candles for warm-up and starts execution at the first OOS candle open.
- Added configurable fold count, warm-up length, fixed test-window length and embargo gap.
- Aggregated net expectancy, net profit factor, net win rate, compounded return and drawdown across OOS folds.
- Added `walk_forward_backtest(df, tf, symbol="")` in `app.py` to reuse current Spot/Futures, fee, spread/slippage, leverage and funding settings.
- Added deterministic tests for fold boundaries, OOS execution timing, embargo, input validation and app configuration routing.
- This is frozen-signal validation, not parameter optimization or model training.

## 6.0.0 — Quant Intelligence & Future Prediction Foundation

### Critical (P0)
- **Backtest engine** rewritten: long/short support, explicit spread/slippage, full trade log, MFE/MAE, autopsy summary, richer metrics, clearer execution assumptions.
- **Statistical Edge Engine** (`statistical_edge.py`): forward-return correlation, effective sample size, t-statistics, multi-horizon analysis, explicit "INSUFFICIENT STATISTICAL EVIDENCE" / NO_TRADE recommendation.
- **Forecast Engine** (`forecast_engine.py`): multi-horizon (1/3/6/12/24/48+) moving-block bootstrap distributions, P10–P90 price quantiles, drift only when statistically supported, scenario skeletons, experimental directional probabilities clearly labelled uncalibrated.
- Data integrity, no-lookahead score computation, and agent_core trade gate (EV / net R reconciliation) preserved and reinforced.
- VERSION bumped to 6.0.0.

### High (P1 foundations)
- Forecast journal continues via `agent_core` (immutable records, later resolve).
- Strategy Autopsy hooks via `autopsy_summary` on the trade log.
- Modular documentation and package layout preparation under `docs/` and `chartanalyzer/`.

### Not claimed in this release
- Full production LSTM / Transformer / XGBoost ensemble with adaptive walk-forward model selection.
- Complete institutional SMC (validated order blocks, liquidity maps, sweeps).
- Order-book level execution simulator.
- Fully calibrated real-world directional probabilities (journal must accumulate first).

### Compatibility
- Existing HTTP API and config.json remain the primary interface.
- `pip install -r requirements.txt` then `python app.py` still launches the server.
- Prior tests for fees and no-lookahead retained and extended.

### Migration notes
- Backtest return shape is richer (trades_log, equity_curve, long/short counts). Callers expecting only V5 keys continue to work; new keys are additive.
- Forecast directional probabilities must not be treated as calibrated edge until journal evaluation reports otherwise.

## 6.1.0 - 2026-11-13

- Market-aware backtesting for Spot and Futures.
- Spot is enforced long-only; Futures supports configurable long/short and synthetic leverage.
- Transaction costs scale with notional exposure and configured spread/slippage is passed into the engine.
- Added net trade returns, net expectancy and net profit factor alongside gross metrics.
- Added explicit reporting for funding charges and unmodelled liquidation/margin risk.


## V6.6.0
- Added descriptive Trade Intelligence Engine: side, entry regime, signal-quality band, exit reason, holding duration, MFE/MAE hypotheses, and gross-to-net cost-drag proxy.
- Added trade-intelligence summary to frozen-signal walk-forward output.
- Suppressed definitive margin-health verdicts when mark/entry price-scale mismatch exceeds 30%; status becomes `unverified_inputs`.

## V6.6.1
- Suppress all mark-derived margin risk metrics when entry/mark price scale is suspicious; render N/A in dashboard.
- Add per-trade fee, spread/slippage, and funding drag attribution.
- Display per-fold Buy & Hold and excess-return comparison in Walk-Forward dashboard.
- Update release version metadata.


## V6.6.2
- Render complete Trade Intelligence diagnostics in Backtest and Walk-Forward panels, including average per-trade fee, spread/slippage and configured funding drag.
- Make the open-to-open limitation of MFE/MAE explicit in the dashboard.
- Add a cost-adjusted passive-long benchmark using matching notional exposure, entry/exit fees, spread/slippage and configured constant funding.
- Show gross/net benchmark returns, benchmark cost drag and net excess return per OOS fold and across the sequential OOS run.
- `buyhold_pct` now aliases `buyhold_net_pct`; raw asset return is available as `buyhold_asset_pct`.
- Add regression tests for benchmark costs, funding, fold comparisons and dashboard rendering.
- Attach causal pre-fill signal score and entry regime to Walk-Forward trades so Trade Intelligence does not silently group them as unknown.
- Correct the stored per-trade cost-drag basis to use gross account return when available.
- Correct cost-drag attribution for leveraged/fractional positions by comparing net trade return against gross account-level return at matching notional exposure; standardize MAE as a signed non-positive adverse return so the threshold diagnostics are consistent.


## V6.6.3
- Separate gross/net win-rate accounting and net winner/loser/breakeven counts.
- Add per-trade Trade Forensics audit rows.
- Add frozen-signal execution-cost stress scenarios at 1x/1.5x/2x/3x.
- No signal rules or parameter optimization changed.


## V6.6.4

- Added entry score cohort diagnostics and MFE-based exit capture research.
- Added retrospective analysis of positive sub-threshold bullish candidates at 1/3/6/12-bar horizons.
- Explicitly labels rejected-candidate results as overlapping, raw, cost-free retrospective observations.
- No signal, parameter, or optimization changes.
- Clarified that Data Integrity defaults to 800 candles while Walk-Forward requests 1200, explaining the 799 vs 1199 closed-candle report.

## V6.6.5
- Added evidence-first Research Findings to each backtest: small-sample warnings, negative expectancy/PF, passive benchmark underperformance, signal-only exit mix, regime breadth, losing trades after positive favorable excursion, and a carefully gated MFE-versus-cost hurdle check for 1x spot only.
- Corrected Exit Capture so gross MFE capture is calculated only for positive-gross-return trades. Losing trades with positive MFE are counted separately; a loss divided by MFE is no longer presented as a misleading capture ratio.
- Added explicit per-trade capture status labels and average open-to-open MFE/MAE and recorded cost drag summaries.
- Fixed the backtest UI's missing `num` formatter scope, added a 60-second request timeout, and surfaced display/network errors rather than leaving the panel at `running...`.
- No signal, threshold, indicator, or strategy parameters were changed. All new findings are descriptive and non-optimizing.


## V6.7.0

- Walk-Forward now enforces restricted entry hours and dynamic indicator warm-up.
- Backtests simulate ATR-based intrabar stop-loss/take-profit using OHLC, with conservative stop-first handling for ambiguous bars.
- Added Signal Forensics with entry component scores, regime/conviction cohorts, and OHLC excursion diagnostics.
- Added 5 tests for the new execution and diagnostics behavior; total suite: 133 passing.

## [6.8.0] - 2026-11-14
### Added
- Trade Failure Intelligence: per-trade OHLC path reconstruction, entry/pre-exit score and component changes, first favorable excursion timestamps, ATR level touch ordering, descriptive failure tags, and cost-aware counterfactual early time exits.
- Walk-Forward dashboard panel for per-trade failure records and early-exit scenario summaries.
### Fixed
- Intrabar MFE/MAE now includes the exit candle for ATR stop/target fills while excluding it for signal exits filled at the candle open.
### Unchanged
- Frozen signal, indicator weights, entry thresholds, and strategy rules; no parameter optimization.

## [6.9.0] - Trade Edge Discovery V1
- Added standalone executed-trade edge research with net account returns, side splits, bootstrap intervals, conditional cohorts, and approximate cost stress.
- Research-only: no signal, threshold, or live execution behavior changed.
- Added five focused tests; full suite: 142 passed.


## [6.9.1] - Execution Viability Gate
### Added
- Integrated Trade Edge Discovery into the backtest response and dashboard.
- Added Execution Viability diagnostics for gross/net returns, modeled cost hurdle, and MFE-to-cost ratio.
- Added five unit tests for execution viability and expanded app integration assertions.
### Unchanged
- Frozen strategy signals, indicator weights, thresholds, and execution rules. No parameter fitting or live-trading authorization.
- Validation: 147 pytest tests passed.

## [6.9.3] - Exit Path Intelligence
### Added
- Per-trade MFE peak candle/timestamp, bars from peak to actual exit, realized gross price return, favorable-excursion giveback, and gross MFE capture ratio.
- Exit-reason cohorts and an auditable per-trade peak/giveback table in Walk-Forward.
- OHLC-aware path boundaries: signal exits exclude the exit candle; intrabar stop/target exits include it.
### Fixed
- Backtest dashboard `pct is not defined` error by moving the percentage formatter into the correct function scope.
### Unchanged
- Frozen signal, indicators, entry/exit rules, thresholds, and execution parameters. Diagnostics are descriptive only; no optimization or profitability claim.
