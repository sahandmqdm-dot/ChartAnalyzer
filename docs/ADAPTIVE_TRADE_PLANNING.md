# Adaptive Trade Planning Engine

## Purpose
Standalone fail-closed position sizing and trade planning. It does not change ChartAnalyzer's signal formula, indicator weights, forecasts, backtest rules, or existing deployment gate.

## API
Import `build_trade_plan` from `adaptive_trade_planner.py`.
Required: `side`, `entry_price`, causal ATR from completed candles, and `account_equity` in quote currency.

Defaults: 0.5% account risk (not 0.5% stop distance), 5x hard leverage ceiling, 1.5 ATR stop, 2R/3R targets, 25% equity margin budget. Fees and slippage must be supplied; zero defaults mean unknown costs, not zero real-world costs.

## Fail-closed
Result is `NO_TRADE` unless the caller explicitly supplies a valid current signal, passed data integrity and matching fresh OOS authorization. If deployment gate payload is supplied, `eligible` must be true. The module never submits orders; `actionable` is always false.

## Sizing
Quantity is the smaller of risk-budget quantity and margin-budget quantity. Modeled round-trip fee/slippage is reserved inside the stop-loss budget. Leverage falls as stop distance/ATR rises to preserve an estimated liquidation buffer.

## Limitations
Linear quote-settled contracts only. Exchange-specific maintenance tiers, mark-price liquidation, funding, order-book depth, partial fills and correlated portfolio exposure are not modeled. Bind every plan to exact symbol, quote currency, timeframe, data fingerprint, strategy fingerprint and fresh OOS gate. Verify exchange rules and live prices before any order.

Run `python -m pytest tests/test_adaptive_trade_planner.py -q`.
