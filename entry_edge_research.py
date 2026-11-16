"""Out-of-sample entry-strength attribution for executed trades only.

This is a pre-defined cohort report, not a parameter optimizer. It uses
realized account-level net returns from the sequential OOS trade log, preserves
chronological order in a moving-block bootstrap, and never changes a signal.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, Optional

import numpy as np

ENGINE_VERSION = "EntryEdgeResearch V1.0"


def _finite(value: Any) -> Optional[float]:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _pf(values: np.ndarray):
    wins = float(values[values > 0].sum())
    losses = float(-values[values < 0].sum())
    if losses <= 1e-12:
        return "INF" if wins > 1e-12 else None
    return round(wins / losses, 5)


def _moving_block_ci(values: np.ndarray, *, iterations: int, seed: int, block_length: int):
    n = len(values)
    if n < 2:
        return None
    block = max(1, min(int(block_length), n))
    blocks_needed = int(math.ceil(n / block))
    rng = np.random.default_rng(seed)
    means = np.empty(iterations, dtype=float)
    # Circular moving blocks preserve local serial dependence better than IID resampling.
    starts = np.arange(n)
    for offset in range(0, iterations, 500):
        count = min(500, iterations - offset)
        chosen = rng.choice(starts, size=(count, blocks_needed), replace=True)
        idx = (chosen[:, :, None] + np.arange(block)[None, None, :]) % n
        sample = values[idx.reshape(count, -1)[:, :n]]
        means[offset:offset + count] = sample.mean(axis=1)
    lo, hi = np.quantile(means, [0.025, 0.975])
    return [round(float(lo * 100.0), 5), round(float(hi * 100.0), 5)]


def _summarize(rows, *, min_samples: int, iterations: int, seed: int, block_length: int):
    values = np.asarray([r["net_ret"] for r in rows], dtype=float)
    n = len(values)
    if not n:
        return {"samples": 0, "status": "NO_SAMPLES", "net_expectancy_pct": None,
                "net_profit_factor": None, "win_rate_pct": None, "bootstrap_ci_95_pct": None}
    ci = _moving_block_ci(values, iterations=iterations, seed=seed, block_length=block_length) if n >= 2 else None
    return {
        "samples": n,
        "status": "EXPLORATORY_SAMPLE" if n >= min_samples else "INSUFFICIENT_SAMPLE",
        "net_expectancy_pct": round(float(values.mean() * 100.0), 5),
        "median_net_return_pct": round(float(np.median(values) * 100.0), 5),
        "net_profit_factor": _pf(values),
        "win_rate_pct": round(float(np.mean(values > 0) * 100.0), 3),
        "bootstrap_ci_95_pct": ci,
        "positive_ci": bool(ci and ci[0] > 0),
        "block_length_trades": max(1, min(int(block_length), n)),
    }


def analyze_entry_edge(trades: Iterable[Dict[str, Any]], *, min_samples: int = 20,
                       bootstrap_iterations: int = 2000, seed: int = 703,
                       score_threshold: float = 0.20) -> Dict[str, Any]:
    """Attribute OOS executed net outcomes to pre-defined directional-score bands."""
    if min_samples < 2 or bootstrap_iterations < 200:
        raise ValueError("min_samples must be >= 2 and bootstrap_iterations >= 200")
    if not math.isfinite(float(score_threshold)) or score_threshold < 0:
        raise ValueError("score_threshold must be finite and >= 0")

    valid, invalid = [], 0
    for order, trade in enumerate(trades or []):
        if not isinstance(trade, dict):
            invalid += 1
            continue
        net = _finite(trade.get("net_ret"))
        score = _finite(trade.get("entry_signal_score"))
        side = str(trade.get("side", "long")).lower()
        if net is None or score is None or side not in {"long", "short"}:
            invalid += 1
            continue
        directional_score = score if side == "long" else -score
        row = dict(trade)
        row.update(net_ret=net, directional_entry_score=directional_score, _chronological_order=order)
        valid.append(row)

    # Fixed bands are diagnostic only; no cut-off is selected from the outcomes.
    threshold = float(score_threshold)
    bands = {"near_threshold": [], "intermediate": [], "high_conviction": []}
    for row in valid:
        score = row["directional_entry_score"]
        if score < threshold + 0.10:
            bands["near_threshold"].append(row)
        elif score < threshold + 0.30:
            bands["intermediate"].append(row)
        else:
            bands["high_conviction"].append(row)

    n = len(valid)
    block_length = max(1, int(round(math.sqrt(n)))) if n else 1
    overall = _summarize(valid, min_samples=min_samples, iterations=bootstrap_iterations,
                         seed=seed, block_length=block_length)
    cohort_results = {}
    for index, (name, rows) in enumerate(bands.items()):
        cohort_results[name] = _summarize(
            rows, min_samples=min_samples, iterations=bootstrap_iterations,
            seed=seed + index + 1,
            block_length=max(1, int(round(math.sqrt(len(rows))))) if rows else 1,
        )

    return {
        "engine": ENGINE_VERSION,
        "status": "RESEARCH_ONLY" if valid else "INSUFFICIENT_DATA",
        "scope": "sequential_walk_forward_oos_executed_trades_only",
        "samples": n,
        "invalid_or_missing": invalid,
        "score_threshold": threshold,
        "overall": overall,
        "directional_score_cohorts": cohort_results,
        "method": "Executed OOS trades only; score is sampled at the signal close before next-open entry; net account returns include the configured execution costs. Cohort boundaries are pre-declared and are not optimized.",
        "guardrails": [
            "No rejected or overlapping forward-return labels are included.",
            "Circular moving-block bootstrap preserves limited local trade-sequence dependence; small samples remain highly uncertain.",
            "A positive cohort point estimate is not an edge unless its confidence interval, sample size, fold stability and untouched future data also support it.",
            "This module is attribution only: it does not alter, filter, rank or authorize live signals.",
        ],
    }


__all__ = ["analyze_entry_edge"]
