# ChartAnalyzer V7.2.0

**Institutional-grade quant intelligence & future-prediction foundation**

Causal multi-timeframe analysis, statistical edge evaluation, multi-horizon distributional forecasts, realistic cost-aware trade gating, and reproducible backtesting.

## Quick start

```bash
cd ChartAnalyzer
python -m venv .venv && source .venv/bin/activate   # optional
pip install -r requirements.txt
python app.py                    # http://127.0.0.1:8765
# or: python app.py --lan
```

Config: `config.json` (see comments inside). Optional CCXT for extra exchanges.

## V7.2.0: Quant Research Engine

- Persistent hypothesis/experiment registry with dataset and configuration lineage.
- Block-bootstrap inference, FDR correction and entry-signal block permutation control.
- Configurable execution delay and separate market-impact allowance.
- Chronological split, purged/embargoed CV and robustness analysis primitives.
- One-way final holdout seal and single-use evaluation endpoint.
- Research Lab panel in the local dashboard.
- Stricter fail-closed gate: >=200 OOS trades, PF >=1.20, <=15% OOS drawdown, positive bootstrap lower bound and FDR q <=0.05.

No strategy profitability is claimed. See `RELEASE_NOTES_V7_2_0_FA.md`.

## V7.1.0: Iran Exchange Intelligence (optional)

- Optional read-only MCP connection to public data from Iranian exchanges.
- Dashboard/API support for cross-exchange price comparison, order books and supported OHLC candles.
- MCP tool schemas are discovered dynamically; no exchange API keys or account permissions are used.
- MCP market data remains separate from the historical research feed and is never silently merged into backtests or OOS samples.
- Windows setup: run `INSTALL_IRAN_MCP_WINDOWS.ps1`; see `IRAN_CRYPTO_MCP_SETUP_FA.md`.

The integration is disabled by default. It does not change the signal formula, execution rules or validation gate. Live upstream connectivity must be verified from the user's machine.

## V7.0.0: Research integrity and deployment safety

- Reproducible run identity from exact normalized OHLCV and complete strategy/execution settings.
- A shared candle window and aligned first executable fill for full-window and OOS reports.
- OOS diagnostic cohort integrity and direction-adjusted entry attribution on executed trades only.
- A fail-closed deployment gate requiring fresh positive net-of-cost Walk-Forward evidence for the exact configuration.
- OHLCV integrity blocking before backtest, Walk-Forward and forecast computation.

The active signal formula and indicator weights remain unchanged. The release improves validation integrity and guards deployment; it does not claim a profitable strategy. See `RELEASE_NOTES_V7_0_0_FA.md`.

## What V6 adds

- **Statistical Edge Engine** — rejects signals that lack evidence (t-stats, effective N, forward-return analysis).
- **Forecast Engine** — multi-horizon block-bootstrap price distributions (P10–P90), drift only when supported, experimental directional probabilities clearly marked uncalibrated.
- **Backtest V6.1** — explicit Spot/Futures modes, Spot long-only enforcement, Futures long/short, exposure-scaled fees and spread/slippage, net trade expectancy and profit factor.
- **Walk-Forward V6.2** — sequential non-overlapping out-of-sample evaluation of a frozen causal signal, with configurable warm-up and embargo windows, per-fold metrics and compounded OOS results.
- **Futures Margin V6.3** — standalone linear isolated-margin estimator for Long/Short, maintenance reserve, additional collateral, estimated liquidation price and optional mark-price health.
- **Signal Forensics V6.7** — dynamic warm-up, restricted-hours entry enforcement, OHLC-based ATR stop/target simulation and entry-component diagnostics.
- **Strategy Autopsy V6.4** — descriptive performance slices by side, entry regime, signal strength, exit reason and holding duration, with MFE/MAE and entry-candle context. No parameter fitting or automatic strategy changes.
- **Preserved** — data integrity blocking, causal indicator scoring, multi-TF weights, agent journal, existing HTTP API.

The Walk-Forward runner evaluates the current frozen signal; it does not optimize parameters or train a model. Historical Futures backtests still use a synthetic notional multiplier and do not simulate margin calls, liquidation events or historical funding schedules. The V6.3 margin calculator is a separate estimate for linear isolated positions; it does not replicate exchange-specific tier brackets, Cross margin, mark-price rules or the liquidation engine. See `RELEASE_NOTES_V6_7_FA.md`, `RELEASE_NOTES_V6_3_FA.md`, `RELEASE_NOTES_V6_2_FA.md`, `RELEASE_NOTES_V6_1_FA.md`, `docs/ARCHITECTURE.md` and `CHANGELOG.md`.

## Tests

```bash
pytest tests/ -q
```

All core mathematical and no-lookahead invariants are covered.

## Important disclaimers

- Forecasts are estimates with uncertainty, not guarantees.
- Bootstrap frequencies ≠ calibrated real-world probabilities until the journal has sufficient resolved outcomes.
- Backtests are bar-level approximations; they do not prove edge.
- NO_TRADE is a first-class, preferred outcome when evidence or economics fail.

## License / usage

Use at your own risk. This is research software, not financial advice.

## V6.5.0

Adds OHLCV and indicator integrity audits, explicit metric semantics/units, fail-closed trade-plan gating for critical integrity failures, and MFI zero-flow edge-case handling. See `RELEASE_NOTES_V6_5_FA.md`.


## Cloud-ready market-data storage

The Binance collector is deployment-portable: configure CHARTANALYZER_DATA_DIR for a local disk or a persistent cloud volume. Optional S3-compatible archival mirrors only closed UTC-day partitions after hash-chain verification. Local raw files are retained, Spot and USD-M stay isolated, and cloud transfer never blocks live capture. See docs/CLOUD_READY_DATA_ARCHITECTURE.md. The optional uploader requires boto3.
