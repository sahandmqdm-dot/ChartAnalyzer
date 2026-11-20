"""Advanced anti-overfitting diagnostics inspired by open quant research tools.

These are research diagnostics only. They do not authorize deployment or replace
chronological out-of-sample validation, execution-cost modeling, or the final holdout.
"""
from __future__ import annotations
from itertools import combinations
from math import e, isfinite, log, sqrt
from statistics import NormalDist

import numpy as np

_EULER_MASCHERONI = 0.5772156649015329

def _finite_vector(values, name="returns"):
    x = np.asarray(values, dtype=float)
    if x.ndim != 1 or len(x) < 3 or not np.isfinite(x).all():
        raise ValueError(f"{name} must be a finite one-dimensional series with >=3 values")
    return x

def deflated_sharpe_ratio(returns, *, n_trials: int, periods_per_year: int = 1,
                          benchmark_sharpe: float = 0.0) -> dict:
    """Estimate PSR against the expected maximum Sharpe from n_trials.

    n_trials must count the actual strategy/parameter trials represented by the
    experiment family. Correlated trials make the independent-trial approximation
    imperfect; report and review that assumption rather than hiding it.
    """
    x = _finite_vector(returns)
    if isinstance(n_trials, bool) or int(n_trials) != n_trials or n_trials < 1:
        raise ValueError("n_trials must be an integer >=1")
    if isinstance(periods_per_year, bool) or int(periods_per_year) != periods_per_year or periods_per_year < 1:
        raise ValueError("periods_per_year must be an integer >=1")
    benchmark = float(benchmark_sharpe)
    if not isfinite(benchmark):
        raise ValueError("benchmark_sharpe must be finite")
    mean = float(np.mean(x))
    std = float(np.std(x, ddof=1))
    if std <= 0 or not isfinite(std):
        raise ValueError("returns must have non-zero finite sample variance")
    sr = mean / std
    centered = x - mean
    m2 = float(np.mean(centered ** 2))
    skew = float(np.mean(centered ** 3) / (m2 ** 1.5)) if m2 > 0 else 0.0
    kurtosis = float(np.mean(centered ** 4) / (m2 ** 2)) if m2 > 0 else 3.0
    variance_term = 1.0 - skew * sr + ((kurtosis - 1.0) / 4.0) * (sr ** 2)
    if variance_term <= 0 or not isfinite(variance_term):
        raise ValueError("invalid Sharpe sampling variance; inspect return distribution")
    sharpe_se = sqrt(variance_term / (len(x) - 1))
    trials = int(n_trials)
    if trials == 1:
        expected_max = benchmark
    else:
        normal = NormalDist()
        z1 = normal.inv_cdf(1.0 - 1.0 / trials)
        z2 = normal.inv_cdf(1.0 - 1.0 / (trials * e))
        expected_max = benchmark + sharpe_se * (
            (1.0 - _EULER_MASCHERONI) * z1 + _EULER_MASCHERONI * z2
        )
    z_score = (sr - expected_max) / sharpe_se
    probability = NormalDist().cdf(z_score)
    annualizer = sqrt(int(periods_per_year))
    return {
        "status": "DIAGNOSTIC",
        "sharpe": sr,
        "annualized_sharpe": sr * annualizer,
        "expected_max_sharpe_threshold": expected_max,
        "sharpe_standard_error": sharpe_se,
        "deflated_sharpe_probability": probability,
        "z_score": z_score,
        "observations": len(x),
        "trials": trials,
        "skewness": skew,
        "kurtosis_non_excess": kurtosis,
        "method": "Probabilistic Sharpe Ratio against Euler-approx expected max Sharpe",
        "limitations": [
            "trial count must reflect the full searched experiment family",
            "expected-maximum approximation assumes independent Gaussian Sharpe estimates",
            "not a substitute for chronological OOS or execution-cost validation",
        ],
    }

def probability_of_backtest_overfitting(performance, *, max_splits: int = 10000) -> dict:
    """CSCV/PBO from a time-block x strategy performance matrix.

    Each cell must be the same predeclared, comparable net-of-cost metric for one
    strategy on one chronological block. Evenly split blocks form complementary
    in-sample/out-of-sample combinations. Higher values are assumed better.
    """
    matrix = np.asarray(performance, dtype=float)
    if matrix.ndim != 2 or matrix.shape[0] < 4 or matrix.shape[1] < 2:
        raise ValueError("performance must be a blocks x strategies matrix (>=4 blocks, >=2 strategies)")
    if matrix.shape[0] % 2:
        raise ValueError("CSCV requires an even number of chronological blocks")
    if not np.isfinite(matrix).all():
        raise ValueError("performance matrix must be finite; do not silently drop missing blocks")
    if isinstance(max_splits, bool) or int(max_splits) != max_splits or max_splits < 1:
        raise ValueError("max_splits must be an integer >=1")
    blocks, strategies = matrix.shape
    half = blocks // 2
    total_splits = len(list(combinations(range(blocks), half)))
    if total_splits > int(max_splits):
        raise ValueError(f"CSCV requires {total_splits} splits, above max_splits={max_splits}")
    lambdas = []
    selected_ranks = []
    for in_indices in combinations(range(blocks), half):
        in_mask = np.ones(blocks, dtype=bool)
        in_mask[list(in_indices)] = False
        out_indices = np.flatnonzero(in_mask)
        in_scores = matrix[list(in_indices), :].mean(axis=0)
        winner = int(np.argmax(in_scores))
        out_scores = matrix[out_indices, :].mean(axis=0)
        selected = float(out_scores[winner])
        less = int(np.sum(out_scores < selected))
        equal = int(np.sum(out_scores == selected))
        average_rank_ascending = less + (equal + 1) / 2.0
        omega = average_rank_ascending / (strategies + 1.0)
        omega = min(1.0 - 1e-12, max(1e-12, omega))
        lambdas.append(log(omega / (1.0 - omega)))
        selected_ranks.append(omega)
    pbo = float(np.mean(np.asarray(lambdas) <= 0.0))
    return {
        "status": "DIAGNOSTIC",
        "pbo": pbo,
        "splits": len(lambdas),
        "blocks": blocks,
        "strategies": strategies,
        "median_logit_rank": float(np.median(lambdas)),
        "mean_logit_rank": float(np.mean(lambdas)),
        "selected_oos_relative_ranks": selected_ranks,
        "method": "Combinatorially Symmetric Cross-Validation (CSCV)",
        "limitations": [
            "blocks must be chronological, comparable and fixed before inspecting results",
            "strategy set and all attempted variants must be registered",
            "PBO diagnoses selection overfit; it is not a forward live-performance probability",
            "overlapping trades or dependent blocks require additional care",
        ],
    }

__all__ = ["deflated_sharpe_ratio", "probability_of_backtest_overfitting"]
