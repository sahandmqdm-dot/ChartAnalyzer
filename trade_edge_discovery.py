"""Trade Edge Discovery V1 for ChartAnalyzer.

Consumes *executed backtest trades* (not rejected-signal labels or overlapping
forward returns). It is research-only: it never changes signals or authorizes
live orders. Expects account-level net_ret from backtest_engine where possible.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional

import numpy as np

ENGINE_VERSION = "TradeEdgeDiscovery V1.0"


def _number(value: Any) -> Optional[float]:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _max_drawdown(returns: np.ndarray) -> float:
    if not len(returns):
        return 0.0
    equity = np.cumprod(1.0 + returns)
    peaks = np.maximum.accumulate(np.concatenate(([1.0], equity)))
    path = np.concatenate(([1.0], equity))
    return float(np.min(path / np.maximum(peaks, 1e-15) - 1.0))


def _bootstrap_mean_ci(values: np.ndarray, *, iterations: int, confidence: float, seed: int):
    if len(values) < 2:
        return None
    rng = np.random.default_rng(seed)
    # Chunking bounds memory for larger histories.
    means = np.empty(iterations, dtype=float)
    chunk = 2000
    for start in range(0, iterations, chunk):
        stop = min(iterations, start + chunk)
        idx = rng.integers(0, len(values), size=(stop - start, len(values)))
        means[start:stop] = values[idx].mean(axis=1)
    alpha = (1.0 - confidence) / 2.0
    lo, hi = np.quantile(means, [alpha, 1.0 - alpha])
    return [round(float(lo), 8), round(float(hi), 8)]


def _metrics(rows: List[Dict[str, Any]], *, min_samples: int, iterations: int,
             confidence: float, seed: int, cost_stress: Iterable[float]) -> Dict[str, Any]:
    vals = np.asarray([r["net_ret"] for r in rows], dtype=float)
    n = len(vals)
    wins, losses = vals[vals > 0], vals[vals < 0]
    gross_profit = float(wins.sum())
    gross_loss = float(-losses.sum())
    pf = gross_profit / gross_loss if gross_loss > 1e-15 else (None if gross_profit <= 0 else float("inf"))
    expectancy = float(vals.mean()) if n else None
    ci = _bootstrap_mean_ci(vals, iterations=iterations, confidence=confidence, seed=seed) if n >= 2 else None
    result = {
        "samples": n,
        "status": "SUFFICIENT_FOR_SCREENING" if n >= min_samples else "INSUFFICIENT_SAMPLES",
        "win_rate_pct": round(float(np.mean(vals > 0) * 100), 3) if n else None,
        "net_expectancy_pct": round(expectancy * 100, 5) if expectancy is not None else None,
        "median_net_return_pct": round(float(np.median(vals) * 100), 5) if n else None,
        "net_profit_factor": round(float(pf), 5) if pf is not None and math.isfinite(pf) else ("INF" if pf == float("inf") else None),
        "max_drawdown_trade_sequence_pct": round(_max_drawdown(vals) * 100, 4) if n else None,
        "mean_win_pct": round(float(wins.mean() * 100), 5) if len(wins) else None,
        "mean_loss_pct": round(float(losses.mean() * 100), 5) if len(losses) else None,
        "expectancy_bootstrap_ci_pct": [round(x * 100, 5) for x in ci] if ci else None,
        "cost_stress": {},
    }
    drags = np.asarray([r["cost_drag"] for r in rows if r.get("cost_drag") is not None], dtype=float)
    for mult in cost_stress:
        mult = float(mult)
        if not math.isfinite(mult) or mult < 1.0:
            continue
        # Incremental drag is estimated from the backtest's gross/net account returns.
        stressed = vals.copy()
        if len(drags) == len(vals):
            stressed = stressed - (mult - 1.0) * drags
        stress_mean = float(stressed.mean()) if len(stressed) else None
        result["cost_stress"][str(mult)] = {
            "net_expectancy_pct": round(stress_mean * 100, 5) if stress_mean is not None else None,
            "positive_expectancy": bool(stress_mean > 0) if stress_mean is not None else None,
            "method": "linear_incremental_cost_drag_estimate; rerun execution backtest for authoritative result",
        }
    return result


def analyze_trade_edge(
    trades: Iterable[Dict[str, Any]], *, min_samples: int = 30,
    bootstrap_iterations: int = 5000, confidence: float = 0.95,
    seed: int = 1701, cost_stress: Iterable[float] = (1.0, 1.5, 2.0),
    group_fields: Iterable[str] = ("market_regime", "entry_type", "exit_reason"),
) -> Dict[str, Any]:
    """Analyze completed trades with realized net returns.

    Required field: net_ret (decimal account return, e.g. 0.01 = +1%).
    Optional: gross_account_ret, side, market_regime, entry_type, exit_reason.
    Missing net returns are excluded and counted. Group labels must be attached
    causally at entry/exit by the caller; this function does not infer them.
    """
    if min_samples < 2:
        raise ValueError("min_samples must be >= 2")
    if bootstrap_iterations < 500:
        raise ValueError("bootstrap_iterations must be >= 500")
    if not 0.5 < confidence < 1.0:
        raise ValueError("confidence must be between 0.5 and 1.0")

    valid: List[Dict[str, Any]] = []
    excluded = 0
    for trade in trades or []:
        if not isinstance(trade, dict):
            excluded += 1
            continue
        net = _number(trade.get("net_ret"))
        if net is None or net <= -1.0:
            excluded += 1
            continue
        gross = _number(trade.get("gross_account_ret"))
        row = dict(trade)
        row["net_ret"] = net
        row["cost_drag"] = max(0.0, gross - net) if gross is not None else None
        row["side"] = str(trade.get("side", "unknown")).lower()
        valid.append(row)

    overall = _metrics(valid, min_samples=min_samples, iterations=bootstrap_iterations,
                       confidence=confidence, seed=seed, cost_stress=cost_stress)
    by_side: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in valid:
        by_side[row["side"]].append(row)
    side_reports = {
        side: _metrics(rows, min_samples=min_samples, iterations=bootstrap_iterations,
                       confidence=confidence, seed=seed + i + 1, cost_stress=cost_stress)
        for i, (side, rows) in enumerate(sorted(by_side.items()))
    }

    groups: Dict[str, Dict[str, Any]] = {}
    for field in group_fields:
        buckets: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for row in valid:
            label = row.get(field)
            if label is not None and str(label).strip():
                buckets[str(label)].append(row)
        groups[field] = {
            label: _metrics(rows, min_samples=min_samples, iterations=bootstrap_iterations,
                            confidence=confidence, seed=seed + len(groups) + j + 11,
                            cost_stress=cost_stress)
            for j, (label, rows) in enumerate(sorted(buckets.items()))
        }

    ci = overall.get("expectancy_bootstrap_ci_pct")
    pf = overall.get("net_profit_factor")
    enough = overall["samples"] >= min_samples
    pf_ok = isinstance(pf, (int, float)) and pf >= 1.2 or pf == "INF"
    ci_positive = bool(ci and ci[0] > 0)
    exp_positive = (overall.get("net_expectancy_pct") or 0) > 0
    if not enough:
        verdict = "INSUFFICIENT_EVIDENCE"
    elif exp_positive and pf_ok and ci_positive:
        verdict = "PROMISING_REQUIRES_WALK_FORWARD"
    elif exp_positive:
        verdict = "POSITIVE_POINT_ESTIMATE_NOT_CONFIRMED"
    else:
        verdict = "NO_DEMONSTRATED_NET_EDGE"

    return {
        "engine": ENGINE_VERSION,
        "status": "RESEARCH_ONLY",
        "verdict": verdict,
        "overall": overall,
        "by_side": side_reports,
        "conditional_groups": groups,
        "data_quality": {
            "input_trades": len(valid) + excluded,
            "valid_executed_trades": len(valid),
            "excluded_missing_or_invalid_net_return": excluded,
            "net_return_source": "trade.net_ret (account-level, after modeled costs)",
        },
        "guardrails": [
            "Executed backtest trades only; rejected-signal forward labels are not accepted as realized trades.",
            "Bootstrap intervals assume the supplied trade sequence is an adequate resampling unit; serial dependence can make them optimistic.",
            "Cost stress is an estimate; rerun the execution backtest with stressed fees/spread/slippage for authoritative results.",
            "Conditional labels must be computed using information available at the relevant decision time.",
            "Promising results require chronological walk-forward and untouched out-of-sample validation.",
            "This module does not alter signals, optimize parameters, or authorize live trading.",
        ],
    }


__all__ = ["analyze_trade_edge"]
