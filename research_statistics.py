"""Dependence-aware inference utilities. Research diagnostics, not deployment authorization."""
from __future__ import annotations

import math
import numpy as np


def _clean(x):
    raw = np.asarray(x, dtype=float)
    if raw.ndim != 1:
        raise ValueError("values must be a one-dimensional chronological series")
    if len(raw) < 2:
        raise ValueError("at least two observations are required")
    if not np.isfinite(raw).all():
        raise ValueError(
            "values must be finite; resolve missing observations without compressing the time series"
        )
    return raw


def _positive_int(value, name, *, minimum=1):
    if isinstance(value, (bool, np.bool_)):
        raise ValueError(f"{name} must be an integer >= {minimum}")
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} must be an integer >= {minimum}") from None
    if parsed != value or parsed < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return parsed


def _nonnegative_finite(value, name):
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} must be finite and >= 0") from None
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError(f"{name} must be finite and >= 0")
    return parsed


def moving_block_bootstrap(values, *, block_length=5, n_boot=5000, confidence=0.95, seed=17):
    x = _clean(values)
    n = len(x)
    b = min(_positive_int(block_length, "block_length"), n)
    n_boot = _positive_int(n_boot, "n_boot")
    confidence = float(confidence)
    if not math.isfinite(confidence) or not 0 < confidence < 1:
        raise ValueError("confidence must be finite and strictly between 0 and 1")
    rng = np.random.default_rng(seed)
    starts = np.arange(n)
    means = np.empty(n_boot, dtype=float)
    need = math.ceil(n / b)
    for k in range(n_boot):
        s = rng.choice(starts, size=need, replace=True)
        idx = np.concatenate([(v + np.arange(b)) % n for v in s])[:n]
        means[k] = float(np.mean(x[idx]))
    alpha = (1 - confidence) / 2
    lo, hi = np.quantile(means, [alpha, 1 - alpha])
    return {
        "mean": float(np.mean(x)), "ci": [float(lo), float(hi)],
        "confidence": confidence, "block_length": b, "n": n,
        "bootstrap_samples": n_boot, "method": "circular moving-block bootstrap",
    }


def block_bootstrap_mean_test(values, *, block_length=5, n_boot=5000, seed=23):
    """One-sided H0: mean <= 0, using centered circular block bootstrap null."""
    x = _clean(values)
    observed = float(x.mean())
    centered = x - observed
    n = len(x)
    b = min(_positive_int(block_length, "block_length"), n)
    n_boot = _positive_int(n_boot, "n_boot")
    rng = np.random.default_rng(seed)
    need = math.ceil(n / b)
    null = np.empty(n_boot)
    for k in range(n_boot):
        s = rng.integers(0, n, size=need)
        idx = np.concatenate([(v + np.arange(b)) % n for v in s])[:n]
        null[k] = float(centered[idx].mean())
    p = float((1 + np.sum(null >= observed)) / (len(null) + 1))
    return {
        "statistic": observed, "p_value": p, "n": n, "block_length": b,
        "bootstrap_samples": n_boot,
        "method": "centered circular moving-block bootstrap, one-sided",
    }


def block_signal_permutation(signal, forward_returns, *, cost_per_turnover=0.0,
                              block_length=24, n_permutations=1000, seed=41):
    """Block-order permutation control for a causal long/flat signal.

    Signal blocks are reordered while market returns stay chronological. Costs are
    charged on position changes. This tests timing association, not stop/target execution.
    """
    s = np.asarray(signal, dtype=float).reshape(-1)
    r = np.asarray(forward_returns, dtype=float).reshape(-1)
    if len(s) != len(r) or len(s) < 64:
        raise ValueError("signal/return arrays must match and contain >=64 bars")
    if not np.isfinite(s).all() or not np.isfinite(r).all():
        raise ValueError("non-finite input")
    if not np.isin(s, [0, 1]).all():
        raise ValueError("signal must be binary long/flat")
    cost = _nonnegative_finite(cost_per_turnover, "cost_per_turnover")
    b = min(max(2, _positive_int(block_length, "block_length")), len(s))
    n_permutations = _positive_int(n_permutations, "n_permutations")
    rng = np.random.default_rng(seed)
    blocks = [s[i:i + b] for i in range(0, len(s), b)]

    def score(pos):
        prev = np.r_[0.0, pos[:-1]]
        turnover = np.abs(pos - prev)
        return float(np.mean(pos * r - turnover * cost))

    observed = score(s)
    null = np.empty(n_permutations)
    for k in range(n_permutations):
        order = rng.permutation(len(blocks))
        shuffled = np.concatenate([blocks[i] for i in order])[:len(s)]
        null[k] = score(shuffled)
    p = float((1 + np.sum(null >= observed)) / (len(null) + 1))
    return {
        "observed_mean_net_bar_return": observed, "null_mean": float(null.mean()),
        "null_95pct": [float(v) for v in np.quantile(null, [0.025, 0.975])],
        "p_value_one_sided": p, "n_bars": len(s), "block_length": b,
        "permutations": n_permutations, "method": "block-order signal permutation",
        "scope": "entry timing diagnostic only; excludes ATR stops/targets and is not full-strategy proof",
    }


def benjamini_hochberg(p_values):
    p = np.asarray(p_values, dtype=float)
    if p.ndim != 1 or not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
        raise ValueError("p-values must be finite in [0,1]")
    m = len(p)
    if not m:
        return np.array([], dtype=float)
    order = np.argsort(p)
    ranked = p[order]
    q = np.empty(m)
    running = 1.0
    for i in range(m - 1, -1, -1):
        running = min(running, ranked[i] * m / (i + 1))
        q[order[i]] = min(1.0, running)
    return q


def matched_random_entry_control(signal, forward_returns, *, cost_per_turnover=0.0,
                                  n_simulations=1000, seed=73):
    """Random-entry baseline preserving the observed long-trade holding lengths.

    It randomizes the placement of each contiguous long run over the same sample,
    preserving run lengths and total invested bars. It is a benchmark, not a full
    exchange execution simulation.
    """
    raw_signal = np.asarray(signal, dtype=float).reshape(-1)
    r = np.asarray(forward_returns, dtype=float).reshape(-1)
    if len(raw_signal) != len(r) or len(raw_signal) < 64:
        raise ValueError("signal/return arrays must match and contain >=64 bars")
    if not np.isfinite(raw_signal).all() or not np.isfinite(r).all():
        raise ValueError("non-finite input")
    if not np.isin(raw_signal, [0, 1]).all():
        raise ValueError("signal must be binary long/flat")
    s = raw_signal.astype(np.int8)
    cost = _nonnegative_finite(cost_per_turnover, "cost_per_turnover")
    n_simulations = _positive_int(n_simulations, "n_simulations")
    starts = np.flatnonzero((s == 1) & np.r_[True, s[:-1] == 0])
    ends = np.flatnonzero((s == 1) & np.r_[s[1:] == 0, True]) + 1
    lengths = (ends - starts).astype(int)
    if not len(lengths):
        raise ValueError("observed signal contains no long trades")
    if int(lengths.sum()) > len(s):
        raise ValueError("invalid run-length structure")

    def score(pos):
        turnover = np.abs(pos - np.r_[0, pos[:-1]])
        return float(np.mean(pos * r - turnover * cost))

    observed = score(s.astype(float))
    rng = np.random.default_rng(seed)
    null = np.empty(n_simulations)
    occupied = len(s) - int(lengths.sum())
    for k in range(n_simulations):
        gaps = rng.multinomial(occupied, np.ones(len(lengths) + 1) / (len(lengths) + 1))
        pos = np.zeros(len(s), dtype=float)
        cursor = int(gaps[0])
        for j, length in enumerate(lengths):
            pos[cursor:cursor + length] = 1.0
            cursor += int(length) + int(gaps[j + 1])
        null[k] = score(pos)
    p = float((1 + np.sum(null >= observed)) / (len(null) + 1))
    return {
        "observed_mean_net_bar_return": observed, "random_mean": float(null.mean()),
        "random_95pct": [float(v) for v in np.quantile(null, [0.025, 0.975])],
        "p_value_one_sided": p, "n_bars": len(s), "trade_count": int(len(lengths)),
        "holding_run_lengths": lengths.tolist(), "simulations": n_simulations,
        "method": "matched random entry with preserved holding-run lengths",
        "scope": "entry timing benchmark only; does not model ATR stops/targets or order-book fills",
    }
