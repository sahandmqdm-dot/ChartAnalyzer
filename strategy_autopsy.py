"""ChartAnalyzer V6.4 strategy autopsy.

Descriptive post-trade diagnostics only. This module never changes signals,
parameters, or execution decisions and must not be used as an optimizer.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional

import numpy as np


def _finite(values: Iterable[Any]) -> np.ndarray:
    out = []
    for value in values:
        try:
            x = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(x):
            out.append(x)
    return np.asarray(out, dtype=float)


def _stats(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    r = _finite(t.get("net_ret") for t in trades)
    gross = _finite(t.get("gross_account_ret", t.get("gross_ret")) for t in trades)
    wins, losses = r[r > 0], r[r < 0]
    pf = float(wins.sum() / -losses.sum()) if len(losses) else (math.inf if len(wins) else None)
    gross_r = _finite(t.get("gross_account_ret", t.get("gross_ret")) for t in trades)
    net_wins = int(np.sum(r > 0))
    net_losses = int(np.sum(r < 0))
    net_flat = int(np.sum(r == 0))
    gross_wins = int(np.sum(gross_r > 0))
    return {
        "trades": int(len(r)),
        # Backward-compatible alias is explicitly NET from V6.6.5 onward.
        "win_rate_pct": round(float(np.mean(r > 0) * 100), 2) if len(r) else None,
        "gross_win_rate_pct": round(float(gross_wins / len(gross_r) * 100), 2) if len(gross_r) else None,
        "net_win_rate_pct": round(float(net_wins / len(r) * 100), 2) if len(r) else None,
        "gross_winners": gross_wins,
        "net_winners": net_wins,
        "net_losers": net_losses,
        "net_breakeven": net_flat,
        "net_expectancy_pct": round(float(r.mean() * 100), 4) if len(r) else None,
        "net_profit_factor": round(pf, 4) if pf is not None and math.isfinite(pf) else pf,
        "avg_gross_return_pct": round(float(gross.mean() * 100), 4) if len(gross) else None,
        "avg_holding_bars": round(float(np.mean([int(t.get("bars_held", 0)) for t in trades])), 2) if trades else None,
        "avg_mfe_pct": round(float(np.mean([float(t.get("mfe", 0) or 0) for t in trades]) * 100), 4) if trades else None,
        "avg_mae_pct": round(float(np.mean([float(t.get("mae", 0) or 0) for t in trades]) * 100), 4) if trades else None,
    }


def _group(trades: List[Dict[str, Any]], key: str) -> Dict[str, Any]:
    buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        value = trade.get(key)
        label = "unknown" if value is None else str(value)
        buckets[label].append(trade)
    return {label: _stats(items) for label, items in sorted(buckets.items())}


def analyze_trades(
    trades: List[Dict[str, Any]], *, strategy_pct: Optional[float] = None,
    buyhold_pct: Optional[float] = None, max_dd_pct: Optional[float] = None,
    min_sample: int = 30,
) -> Dict[str, Any]:
    """Return diagnostic slices by side, market regime, exit reason and signal strength."""
    clean = [t for t in (trades or []) if isinstance(t, dict)]
    if not clean:
        return {"version": "6.7.0", "n_trades": 0, "verdict": "INSUFFICIENT_DATA",
                "message": "No completed trades are available for autopsy."}

    enriched = []
    for t in clean:
        row = dict(t)
        bars = int(row.get("bars_held", 0) or 0)
        row["holding_bucket"] = "1-3" if bars <= 3 else "4-12" if bars <= 12 else "13-48" if bars <= 48 else "49+"
        enriched.append(row)

    net = _finite(t.get("net_ret") for t in enriched)
    winners = [t for t in enriched if float(t.get("net_ret", 0) or 0) > 0]
    losers = [t for t in enriched if float(t.get("net_ret", 0) or 0) < 0]
    avg_mfe = float(np.mean([float(t.get("mfe", 0) or 0) for t in enriched]))
    avg_mae = float(np.mean([float(t.get("mae", 0) or 0) for t in enriched]))
    missed = [t for t in losers if float(t.get("mfe", 0) or 0) > 0.002]
    adverse = [t for t in losers if abs(float(t.get("mae", 0) or 0)) > 0.01]
    best = sorted(enriched, key=lambda t: float(t.get("net_ret", 0) or 0), reverse=True)[:5]
    worst = sorted(enriched, key=lambda t: float(t.get("net_ret", 0) or 0))[:5]

    n = len(net)
    warnings = []
    if n < min_sample:
        warnings.append(f"Only {n} trades; below the {min_sample}-trade diagnostic threshold.")
    if len(losers) == n:
        warnings.append("Every completed trade was net-negative in this sample.")
    if strategy_pct is not None and buyhold_pct is not None and strategy_pct < buyhold_pct:
        warnings.append("Strategy underperformed buy-and-hold over the tested window.")
    if avg_mae < -0.01:
        warnings.append("Average adverse excursion exceeded 1%; review entry timing and stop discipline.")

    return {
        "version": "6.7.0", "n_trades": n,
        "verdict": "NEGATIVE_SAMPLE" if len(losers) > len(winners) and (np.mean(net) < 0) else "MIXED_OR_POSITIVE_SAMPLE",
        "overall": _stats(enriched),
        "comparison": {"strategy_pct": strategy_pct, "buyhold_pct": buyhold_pct, "max_dd_pct": max_dd_pct},
        "by_side": _group(enriched, "side"),
        "by_regime": _group(enriched, "entry_regime"),
        "by_exit_reason": _group(enriched, "exit_reason"),
        "by_signal_band": _group(enriched, "entry_signal_band"),
        "by_holding_duration": _group(enriched, "holding_bucket"),
        "diagnostics": {
            "losers_with_positive_mfe_over_0_2pct": len(missed),
            "losers_with_mae_over_1pct": len(adverse),
            "avg_mfe_pct": round(avg_mfe * 100, 4),
            "avg_mae_pct": round(avg_mae * 100, 4),
            "interpretation": "Positive MFE in losing trades may indicate exit/target capture issues; large MAE may indicate entry timing or risk-control issues. These are hypotheses, not causal proof.",
        },
        "best_trades": [{k: t.get(k) for k in ("side", "entry_bar", "exit_bar", "bars_held", "net_ret_pct", "mfe", "mae", "exit_reason")} for t in best],
        "worst_trades": [{k: t.get(k) for k in ("side", "entry_bar", "exit_bar", "bars_held", "net_ret_pct", "mfe", "mae", "exit_reason")} for t in worst],
        "warnings": warnings,
        "guardrail": "Descriptive diagnostics only. No parameter optimization, rule changes, or profitability claims are performed.",
    }


__all__ = ["analyze_trades"]
