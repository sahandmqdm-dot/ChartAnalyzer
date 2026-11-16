"""ChartAnalyzer V7.2.0 walk-forward out-of-sample validation.

This module evaluates a frozen, already-generated signal series on sequential,
non-overlapping test windows. It does not tune parameters or claim that a
strategy has predictive edge. Each fold receives only preceding candles as
warm-up context; orders are first filled at the first OOS candle open.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

import numpy as np

from backtest_engine import run_backtest


def run_walk_forward(
    df,
    signal,
    *,
    n_splits: int = 5,
    warmup_bars: int = 120,
    gap_bars: int = 0,
    test_bars: Optional[int] = None,
    engine_kwargs: Optional[Dict[str, Any]] = None,
    entry_regime=None, entry_allowed=None, entry_components=None,
    entry_market_regime=None, entry_regime_transition=None,
    include_diagnostics: bool = True,
) -> Dict[str, Any]:
    """Run sequential out-of-sample folds on a fixed signal series.

    Parameters
    ----------
    df, signal:
        Full chronological candle frame and same-length causal signal array.
    n_splits:
        Number of non-overlapping OOS folds when ``test_bars`` is omitted.
    warmup_bars:
        Number of preceding candles retained for indicator/state warm-up.
        The first trade decision is made on the close immediately before the
        OOS window and is filled at the first OOS open.
    gap_bars:
        Unscored candles between OOS folds (embargo). They are not traded.
    test_bars:
        Optional fixed length for every fold. The final incomplete remainder
        is ignored rather than silently creating a shorter fold.
    engine_kwargs:
        Keyword arguments forwarded to ``backtest_engine.run_backtest``.

    Notes
    -----
    Signals must be causal: signal[t] may use data through candle t only.
    This routine evaluates a frozen signal, it does not fit or optimize it.
    """
    n = len(df)
    sig = np.asarray(signal, dtype=float)
    if n != len(sig):
        raise ValueError("signal length must match candles")
    regimes = None if entry_regime is None else np.asarray(entry_regime, dtype=object)
    if regimes is not None and len(regimes) != n:
        raise ValueError("entry_regime length must match candles")
    market_regimes = None if entry_market_regime is None else np.asarray(entry_market_regime, dtype=object)
    if market_regimes is not None and len(market_regimes) != n:
        raise ValueError("entry_market_regime length must match candles")
    regime_transitions = None if entry_regime_transition is None else np.asarray(entry_regime_transition, dtype=bool)
    if regime_transitions is not None and len(regime_transitions) != n:
        raise ValueError("entry_regime_transition length must match candles")
    allowed = None if entry_allowed is None else np.asarray(entry_allowed, dtype=bool)
    if allowed is not None and len(allowed) != n:
        raise ValueError("entry_allowed length must match candles")
    components = {}
    for key, values in (entry_components or {}).items():
        arr = np.asarray(values, dtype=float)
        if len(arr) != n:
            raise ValueError(f"entry component '{key}' length must match candles")
        components[str(key)] = arr
    if not np.isfinite(sig).all():
        raise ValueError("signal contains non-finite values")
    if not isinstance(warmup_bars, int) or warmup_bars < 2:
        raise ValueError("warmup_bars must be an integer >= 2")
    if not isinstance(gap_bars, int) or gap_bars < 0:
        raise ValueError("gap_bars must be an integer >= 0")
    if test_bars is not None and (not isinstance(test_bars, int) or test_bars < 32):
        raise ValueError("test_bars must be an integer >= 32")
    if test_bars is None and (not isinstance(n_splits, int) or n_splits < 2):
        raise ValueError("n_splits must be an integer >= 2")
    if n <= warmup_bars:
        raise ValueError("not enough candles after warm-up")

    if test_bars is None:
        available = n - warmup_bars - gap_bars * (n_splits - 1)
        fold_size = available // n_splits
    else:
        fold_size = test_bars
        n_splits = (n - warmup_bars + gap_bars) // (fold_size + gap_bars)
    if fold_size < 32:
        raise ValueError("each OOS fold must contain at least 32 candles")
    if n_splits < 1:
        raise ValueError("not enough candles for the requested walk-forward layout")

    kwargs = dict(engine_kwargs or {})
    base = int(kwargs.pop("start", 1))
    if base < 1:
        raise ValueError("engine start must be >= 1")
    folds = []
    all_trades = []
    combined_curve = []
    equity_scale = 1.0
    buyhold_net_scale = 1.0
    buyhold_gross_scale = 1.0
    used = 0
    blocked_oos_bars = 0

    for fold_no in range(n_splits):
        test_start = warmup_bars + fold_no * (fold_size + gap_bars)
        test_end = test_start + fold_size
        if test_end > n:
            break
        context_start = max(0, test_start - warmup_bars)
        local_start = test_start - context_start - 1
        if local_start < base:
            # Preserve the engine's required warm-up while allowing its start
            # parameter to be explicitly larger than the default.
            context_start = max(0, test_start - base - 1)
            local_start = test_start - context_start - 1
        fold_df = df.iloc[context_start:test_end].copy()
        fold_signal = sig[context_start:test_end]
        fold_regime = regimes[context_start:test_end] if regimes is not None else None
        fold_market_regime = market_regimes[context_start:test_end] if market_regimes is not None else None
        fold_regime_transition = regime_transitions[context_start:test_end] if regime_transitions is not None else None
        fold_allowed = allowed[context_start:test_end] if allowed is not None else None
        fold_kwargs = dict(kwargs)
        if fold_kwargs.get("atr") is not None:
            fold_kwargs["atr"] = np.asarray(fold_kwargs["atr"], dtype=float)[context_start:test_end]
        result = run_backtest(
            fold_df,
            fold_signal,
            start=local_start,
            entry_allowed=fold_allowed,
            **fold_kwargs,
        )
        # The backtest's first executable decision is the close immediately
        # before test_start, so no pre-OOS positions are carried into a fold.
        fold_trades = result.get("trades_log", [])
        threshold = float(kwargs.get("threshold", 0.0))
        for trade in fold_trades:
            trade["global_entry_bar"] = int(context_start + int(trade.get("entry_bar", 0)))
            trade["global_exit_bar"] = int(context_start + int(trade.get("exit_bar", 0)))
            # Entry context is sampled from the signal candle immediately before
            # the next-open fill, never from the fill candle or later data.
            delay = int(kwargs.get("execution_delay_bars", 0))
            signal_idx = max(0, min(len(fold_signal) - 1, int(trade.get("entry_bar", 1)) - 1 - delay))
            score = float(fold_signal[signal_idx])
            trade["global_entry_signal_bar"] = int(context_start + signal_idx)
            trade["entry_signal_score"] = round(score, 6)
            trade["entry_signal_band"] = (
                "bullish" if score >= threshold else
                "bearish" if score <= -threshold else "neutral"
            )
            if fold_regime is not None:
                value = fold_regime[signal_idx]
                trade["entry_regime"] = str(value) if value is not None else "unknown"
            if fold_market_regime is not None:
                value = fold_market_regime[signal_idx]
                trade["entry_market_regime"] = str(value) if value is not None else "unknown"
            if fold_regime_transition is not None:
                trade["entry_regime_transition"] = bool(fold_regime_transition[signal_idx])
            for key, values in components.items():
                trade[f"entry_component_{key}"] = round(float(values[context_start + signal_idx]), 6)
            fill_global_idx = context_start + signal_idx + 1 + int(kwargs.get("execution_delay_bars", 0))
            trade["entry_fill_allowed"] = bool(allowed[fill_global_idx]) if allowed is not None and fill_global_idx < n else None
            if not (test_start <= trade["global_entry_bar"] < test_end):
                raise RuntimeError("walk-forward integrity failure: trade entry escaped its OOS fold")
            if not (test_start <= trade["global_exit_bar"] < test_end):
                raise RuntimeError("walk-forward integrity failure: trade exit escaped its OOS fold")
        all_trades.extend(fold_trades)
        for value in result.get("equity_curve", []):
            combined_curve.append(float(value) * equity_scale)
        equity_scale *= 1.0 + float(result.get("strategy_pct", 0.0)) / 100.0
        buyhold_net_scale *= 1.0 + float(result.get("buyhold_net_pct", result.get("buyhold_pct", 0.0))) / 100.0
        buyhold_gross_scale *= 1.0 + float(result.get("buyhold_gross_pct", result.get("buyhold_pct", 0.0))) / 100.0
        folds.append({
            "fold": fold_no + 1,
            "test_start_index": int(test_start),
            "test_end_index_exclusive": int(test_end),
            "context_start_index": int(context_start),
            "oos_bars": int(test_end - test_start),
            "trades": int(result.get("trades", 0)),
            "strategy_pct": result.get("strategy_pct"),
            "net_expectancy_pct": result.get("net_expectancy_pct"),
            "net_profit_factor": result.get("net_profit_factor"),
            "net_win_rate_pct": result.get("net_win_rate_pct"),
            "max_dd_pct": result.get("max_dd_pct"),
            "buyhold_pct": result.get("buyhold_net_pct", result.get("buyhold_pct")),
            "buyhold_net_pct": result.get("buyhold_net_pct", result.get("buyhold_pct")),
            "buyhold_gross_pct": result.get("buyhold_gross_pct"),
            "buyhold_asset_pct": result.get("buyhold_asset_pct"),
            "buyhold_cost_drag_pct": result.get("buyhold_cost_drag_pct"),
            "excess_vs_buyhold_pct": round(float(result.get("strategy_pct", 0.0)) - float(result.get("buyhold_net_pct", result.get("buyhold_pct", 0.0))), 4),
            "result": result,
        })
        used += fold_size
        if allowed is not None:
            blocked_oos_bars += int((~allowed[test_start:test_end]).sum())

    if not folds:
        raise ValueError("no complete out-of-sample folds were generated")

    net = np.asarray([float(t.get("net_ret", 0.0)) for t in all_trades], dtype=float)
    winners = net[net > 0]
    losers = net[net < 0]
    if len(losers):
        profit_factor = float(winners.sum() / -losers.sum())
    elif len(winners):
        profit_factor = math.inf
    else:
        profit_factor = None
    expectancy = float(net.mean()) if len(net) else None
    win_rate = float(np.mean(net > 0) * 100.0) if len(net) else None

    curve = np.asarray(combined_curve, dtype=float)
    if len(curve):
        peaks = np.maximum.accumulate(np.maximum(curve, 1e-12))
        max_dd = float(np.min(curve / peaks - 1.0) * 100.0)
    else:
        max_dd = None

    if not include_diagnostics:
        return {
            "version": "7.2.0", "evaluation_scope": "sequential_out_of_sample_cost_stress",
            "folds": [{k:v for k,v in f.items() if k!="result"} for f in folds],
            "trades": len(all_trades), "trades_log": all_trades,
            "oos_bars": int(used), "strategy_pct": round((equity_scale-1.0)*100.0,4),
            "buyhold_net_pct": round((buyhold_net_scale-1.0)*100.0,4),
            "net_expectancy_pct": round(expectancy*100.0,4) if expectancy is not None else None,
            "net_profit_factor": round(profit_factor,4) if profit_factor is not None and math.isfinite(profit_factor) else profit_factor,
            "net_win_rate_pct": round(win_rate,2) if win_rate is not None else None,
            "max_dd_pct": round(max_dd,4) if max_dd is not None else None,
            "strategy_frozen": True, "parameter_optimization": False,
            "diagnostics_skipped": True,
        }

    from trade_intelligence import analyze_trade_intelligence
    trade_intelligence = analyze_trade_intelligence(all_trades)
    from trade_edge_discovery import analyze_trade_edge
    regime_edge_rows = []
    for trade in all_trades:
        row = dict(trade)
        row["market_regime"] = row.get("entry_market_regime", "unknown")
        regime_edge_rows.append(row)
    market_regime_edge_research = analyze_trade_edge(
        regime_edge_rows, group_fields=("market_regime",), min_samples=10
    )
    market_regime_transition_edge_research = analyze_trade_edge(
        regime_edge_rows, group_fields=("entry_regime_transition",), min_samples=10
    )
    from exit_path_intelligence import analyze_exit_paths
    exit_path_intelligence = analyze_exit_paths(all_trades, df)
    from trade_failure_intelligence import analyze_trade_failures
    trade_failure_intelligence = analyze_trade_failures(
        all_trades, df, signal=sig, entry_regime=regimes, entry_components=components,
        fee_per_side=float(kwargs.get("fee_per_side", 0.0)),
        spread_slippage_rt=float(kwargs.get("spread_slippage_rt", float(kwargs.get("spread_rt",0.0))+float(kwargs.get("slippage_rt",0.0)))),
        market_impact_rt=float(kwargs.get("market_impact_rt",0.0)),
        funding_fee_per_bar=float(kwargs.get("funding_fee_per_bar", 0.0)),
    )
    from exit_policy_research import analyze_exit_policy_research
    exit_policy_research = analyze_exit_policy_research(
        trade_failure_intelligence.get("trades", [])
    )
    from entry_edge_research import analyze_entry_edge
    entry_edge_research = analyze_entry_edge(
        all_trades, score_threshold=float(kwargs.get("threshold", 0.0))
    )
    from research_provenance import audit_research_cohort
    report_integrity = audit_research_cohort(
        all_trades, trade_intelligence=trade_intelligence,
        exit_path_intelligence=exit_path_intelligence,
        trade_failure_intelligence=trade_failure_intelligence,
        entry_edge_research=entry_edge_research,
    )
    return {
        "version": "7.2.0",
        "evaluation_scope": "sequential_out_of_sample",
        "oos_ranges": [
            {"fold": f["fold"], "start_index": f["test_start_index"],
             "end_index_exclusive": f["test_end_index_exclusive"]}
            for f in folds
        ],
        "entry_edge_research": entry_edge_research,
        "research_cohort_integrity": report_integrity,
        "exit_path_intelligence": exit_path_intelligence,
        "trade_failure_intelligence": trade_failure_intelligence,
        "exit_policy_research": exit_policy_research,
        "trade_intelligence": trade_intelligence,
        "market_regime_edge_research": market_regime_edge_research,
        "market_regime_transition_edge_research": market_regime_transition_edge_research,
        "market_regime_intelligence": {
            "engine": "MarketRegimeIntelligence V1.0", "status": "RESEARCH_ONLY",
            "evaluation": "chronological OOS trades only; frozen signal; modeled costs retained"
        },
        "method": "frozen_signal_sequential_walk_forward",
        "strategy_frozen": True,
        "parameter_optimization": False,
        "folds": folds,
        "n_folds": len(folds),
        "oos_bars": int(used),
        "warmup_bars": int(warmup_bars),
        "entry_hours_filter_applied": allowed is not None,
        "entry_hours_blocked_bars": int(blocked_oos_bars),
        "gap_bars": int(gap_bars),
        "trades": len(all_trades),
        "strategy_pct": round((equity_scale - 1.0) * 100.0, 4),
        "buyhold_pct": round((buyhold_net_scale - 1.0) * 100.0, 4),
        "buyhold_net_pct": round((buyhold_net_scale - 1.0) * 100.0, 4),
        "buyhold_gross_pct": round((buyhold_gross_scale - 1.0) * 100.0, 4),
        "buyhold_cost_drag_pct": round((buyhold_gross_scale - buyhold_net_scale) * 100.0, 4),
        "excess_vs_buyhold_pct": round((equity_scale - buyhold_net_scale) * 100.0, 4),
        "net_expectancy_pct": round(expectancy * 100.0, 4) if expectancy is not None else None,
        "net_profit_factor": round(profit_factor, 4) if profit_factor is not None and math.isfinite(profit_factor) else profit_factor,
        "net_win_rate_pct": round(win_rate, 2) if win_rate is not None else None,
        "max_dd_pct": round(max_dd, 4) if max_dd is not None else None,
        "trades_log": all_trades,
        "equity_curve": combined_curve,
        "warning": (
            "Out-of-sample evaluation of a frozen signal only. Each fold starts flat and is liquidated at its boundary; no position is carried across folds. "
            "No parameter fitting, no guarantee of edge, and no futures liquidation or historical funding model. "
            "Buy-and-hold is a passive-long benchmark with matched notional exposure and configured costs; "
            "constant funding is applied only when configured. "
            "Interpret results cautiously when trade counts are small."
        ),
    }
