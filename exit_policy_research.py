"""Paired, cost-aware research of counterfactual early exits on OOS trades.

Consumes only counterfactuals already reconstructed by trade_failure_intelligence.
It does not search parameters, extend a trade beyond its original exit, change
signals, or produce an executable portfolio backtest.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, Iterable, Optional

import numpy as np

ENGINE_VERSION = "ExitPolicyResearch V1.0"


def _finite(value: Any) -> Optional[float]:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _profit_factor(values: np.ndarray) -> Optional[float]:
    wins = float(values[values > 0].sum())
    losses = float(-values[values < 0].sum())
    if losses <= 1e-12:
        return None if wins <= 1e-12 else None
    return round(wins / losses, 5)


def _bootstrap_mean_ci(values: np.ndarray, *, seed: int, iterations: int) -> Optional[list[float]]:
    if len(values) < 2:
        return None
    rng = np.random.default_rng(seed)
    # Chunking bounds peak memory on unusually large research sets.
    means = np.empty(iterations, dtype=float)
    chunk = 500
    for start in range(0, iterations, chunk):
        size = min(chunk, iterations - start)
        idx = rng.integers(0, len(values), size=(size, len(values)))
        means[start:start + size] = values[idx].mean(axis=1)
    lo, hi = np.quantile(means, [0.025, 0.975])
    return [round(float(lo), 5), round(float(hi), 5)]


def analyze_exit_policy_research(
    trades: Iterable[Dict[str, Any]], *, min_samples: int = 20,
    bootstrap_iterations: int = 2000, seed: int = 47,
) -> Dict[str, Any]:
    """Compare early time-exit estimates with actual net results on paired trades.

    Each horizon includes only trades for which its counterfactual exit occurs
    strictly before the actual exit. Existing ATR barriers are retained by the
    upstream reconstruction. Values are percentage points of account return.
    """
    if min_samples < 2:
        raise ValueError("min_samples must be >= 2")
    if bootstrap_iterations < 200:
        raise ValueError("bootstrap_iterations must be >= 200")

    buckets = defaultdict(list)
    invalid = 0
    for trade in trades or []:
        if not isinstance(trade, dict):
            invalid += 1
            continue
        actual = _finite(trade.get("net_account_return_pct"))
        if actual is None:
            actual = _finite(trade.get("net_ret_pct"))
        if actual is None:
            net = _finite(trade.get("net_ret"))
            actual = net * 100.0 if net is not None else None
        if actual is None:
            invalid += 1
            continue
        scenarios = trade.get("counterfactual_early_time_exits") or []
        seen = set()
        for scenario in scenarios:
            if not isinstance(scenario, dict):
                continue
            horizon = scenario.get("horizon_bars_requested")
            try:
                horizon = int(horizon)
            except (TypeError, ValueError, OverflowError):
                continue
            estimate = _finite(scenario.get("estimated_net_account_return_pct"))
            if estimate is None:
                estimate = _finite(scenario.get("estimated_net_return_pct"))
            if estimate is None or horizon <= 0 or horizon in seen:
                continue
            seen.add(horizon)
            buckets[horizon].append((actual, estimate, bool(scenario.get("same_bar_stop_target_ambiguous", False))))

    summaries = {}
    for horizon, pairs in sorted(buckets.items()):
        actual = np.asarray([p[0] for p in pairs], dtype=float)
        counterfactual = np.asarray([p[1] for p in pairs], dtype=float)
        delta = counterfactual - actual
        n = len(pairs)
        enough = n >= min_samples
        summaries[str(horizon)] = {
            "horizon_bars": horizon,
            "samples": n,
            "status": "RESEARCH_SAMPLE_THRESHOLD_MET" if enough else "INSUFFICIENT_SAMPLE",
            "counterfactual_mean_net_pct": round(float(counterfactual.mean()), 5),
            "counterfactual_median_net_pct": round(float(np.median(counterfactual)), 5),
            "counterfactual_net_win_rate_pct": round(float(np.mean(counterfactual > 0) * 100.0), 3),
            "counterfactual_net_profit_factor": _profit_factor(counterfactual),
            "paired_actual_mean_net_pct": round(float(actual.mean()), 5),
            "paired_mean_net_delta_pct": round(float(delta.mean()), 5),
            "paired_median_net_delta_pct": round(float(np.median(delta)), 5),
            "trades_improved_pct": round(float(np.mean(delta > 1e-12) * 100.0), 3),
            "trades_worsened_pct": round(float(np.mean(delta < -1e-12) * 100.0), 3),
            "same_bar_stop_target_ambiguous": sum(p[2] for p in pairs),
            "paired_delta_bootstrap_95_ci_pct": _bootstrap_mean_ci(
                delta, seed=seed + horizon, iterations=bootstrap_iterations
            ),
            "interpretation": (
                "Paired descriptive counterfactual; not an independently executed strategy."
                if enough else "Too few paired trades for even exploratory cohort interpretation."
            ),
        }

    return {
        "engine": ENGINE_VERSION,
        "status": "RESEARCH_ONLY" if summaries else "NO_COUNTERFACTUAL_SAMPLES",
        "horizons": summaries,
        "horizons_with_minimum_sample": sum(v["samples"] >= min_samples for v in summaries.values()),
        "invalid_trades": invalid,
        "method": (
            "Pairs each eligible early-time-exit estimate with the actual net return of the same OOS trade. "
            "Only horizons strictly earlier than the actual exit are eligible; existing ATR barriers are "
            "honored by the upstream path reconstruction."
        ),
        "guardrails": [
            "Counterfactuals are restricted to earlier exits; they do not extend trades beyond actual exits.",
            "Existing ATR stop/target barriers and modeled costs are retained in the estimated exit return.",
            "OHLC cannot reveal intrabar event ordering; ambiguous same-bar stop/target touches are stop-first upstream.",
            "Overlapping candidate horizons are multiple exploratory comparisons, not independent strategy trials.",
            "No horizon is selected, optimized, or promoted; any hypothesis requires a new untouched OOS period.",
            "This paired trade-level comparison is not a portfolio-level equity simulation and does not model interactions between positions.",
        ],
    }


__all__ = ["analyze_exit_policy_research"]
