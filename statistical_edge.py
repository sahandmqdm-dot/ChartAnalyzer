"""
ChartAnalyzer V6 - Statistical Edge Engine
==========================================
Distinguishes descriptive technical agreement from statistically demonstrated
predictive edge. All calculations are causal (no look-ahead).

Reports INSUFFICIENT STATISTICAL EVIDENCE when sample size, stability or
out-of-sample metrics do not support a claim.
"""
from __future__ import annotations
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple
import numpy as np

# Approximate critical values for two-sided t (large-df ~ normal)
_Z80 = 1.28155
_Z90 = 1.64485
_Z95 = 1.95996


def _finite(a: np.ndarray) -> np.ndarray:
    a = np.asarray(a, dtype=float)
    return a[np.isfinite(a)]


def effective_sample_size(x: Sequence[float], max_lag: int = 40) -> float:
    """HAC-style effective sample size using lag-1 autocorrelation approximation."""
    a = _finite(np.asarray(x, dtype=float))
    n = len(a)
    if n < 10:
        return float(max(n, 1))
    a = a - np.mean(a)
    var = float(np.dot(a, a) / n)
    if var < 1e-18:
        return float(n)
    # lag-1 rho
    rho1 = float(np.dot(a[1:], a[:-1]) / (n * var))
    rho1 = max(-0.99, min(0.99, rho1))
    # AR(1) effective n
    neff = n * (1 - rho1) / (1 + rho1) if abs(rho1) < 0.999 else n / 10.0
    return float(max(5.0, min(n, neff)))


def pearson_corr(x: Sequence[float], y: Sequence[float]) -> Tuple[float, float, float]:
    """Return (corr, n, t_stat). t uses effective sample size."""
    xa = np.asarray(x, dtype=float)
    ya = np.asarray(y, dtype=float)
    mask = np.isfinite(xa) & np.isfinite(ya)
    xa, ya = xa[mask], ya[mask]
    n = len(xa)
    if n < 8:
        return float("nan"), float(n), float("nan")
    xa = xa - xa.mean()
    ya = ya - ya.mean()
    sx = math.sqrt(float(np.dot(xa, xa)))
    sy = math.sqrt(float(np.dot(ya, ya)))
    if sx < 1e-15 or sy < 1e-15:
        return 0.0, float(n), 0.0
    r = float(np.dot(xa, ya) / (sx * sy))
    r = max(-1.0, min(1.0, r))
    neff = effective_sample_size(xa)
    if neff < 5 or abs(r) >= 1.0:
        t = float("nan")
    else:
        t = r * math.sqrt(neff - 2) / math.sqrt(max(1e-12, 1 - r * r))
    return r, float(n), t


def forward_returns(close: Sequence[float], horizon: int) -> np.ndarray:
    """Causal forward log-returns: log(c[t+h]/c[t]). Last h values are NaN."""
    c = np.asarray(close, dtype=float)
    n = len(c)
    out = np.full(n, np.nan)
    if horizon < 1 or n <= horizon:
        return out
    valid = (c[:-horizon] > 0) & (c[horizon:] > 0) & np.isfinite(c[:-horizon]) & np.isfinite(c[horizon:])
    out[:-horizon] = np.where(valid, np.log(c[horizon:] / c[:-horizon]), np.nan)
    return out


def signal_forward_analysis(
    signal: Sequence[float],
    close: Sequence[float],
    horizons: Sequence[int] = (1, 3, 6, 12),
    min_samples: int = 40,
) -> Dict[str, Any]:
    """
    Evaluate whether a continuous signal has any linear association with
    future returns. Never interprets correlation as trading edge by itself.
    """
    sig = np.asarray(signal, dtype=float)
    results = {}
    overall_evidence = "INSUFFICIENT STATISTICAL EVIDENCE"
    best_t = 0.0
    for h in horizons:
        h = int(h)
        fwd = forward_returns(close, h)
        mask = np.isfinite(sig) & np.isfinite(fwd)
        s, f = sig[mask], fwd[mask]
        n = len(s)
        if n < min_samples:
            results[str(h)] = {
                "horizon": h,
                "n": n,
                "status": "INSUFFICIENT_SAMPLES",
                "corr": None,
                "t_stat": None,
                "neff": None,
            }
            continue
        r, n_used, t = pearson_corr(s, f)
        neff = effective_sample_size(s)
        status = "WEAK" if abs(t) < 1.5 else ("MODERATE" if abs(t) < 2.5 else "NOTABLE")
        if abs(t) > abs(best_t) and n_used >= min_samples:
            best_t = t
            if abs(t) >= 2.0 and neff >= 30:
                overall_evidence = "POSSIBLE_EDGE_REQUIRES_OOS_CONFIRMATION"
            elif abs(t) >= 1.5:
                overall_evidence = "WEAK_ASSOCIATION"
        results[str(h)] = {
            "horizon": h,
            "n": int(n_used),
            "neff": round(neff, 1),
            "corr": round(r, 4) if np.isfinite(r) else None,
            "t_stat": round(t, 3) if np.isfinite(t) else None,
            "status": status,
            "note": "Association is not a trading recommendation. Requires walk-forward and cost-adjusted validation.",
        }
    return {
        "engine": "StatisticalEdge V6",
        "evidence_level": overall_evidence,
        "horizons": results,
        "policy": "Reject trade signals that lack out-of-sample confirmation and positive net expected value after costs.",
    }


def regime_conditional_returns(
    close: Sequence[float],
    regime_labels: Sequence[str],
    horizon: int = 6,
    min_per_regime: int = 20,
) -> Dict[str, Any]:
    """Simple regime-conditioned forward return summary."""
    c = np.asarray(close, dtype=float)
    labs = np.asarray(regime_labels)
    fwd = forward_returns(c, horizon)
    out = {}
    for lab in sorted(set(labs)):
        mask = (labs == lab) & np.isfinite(fwd)
        vals = fwd[mask]
        if len(vals) < min_per_regime:
            out[str(lab)] = {"n": len(vals), "status": "INSUFFICIENT"}
            continue
        out[str(lab)] = {
            "n": int(len(vals)),
            "mean_log_ret": round(float(np.mean(vals)), 6),
            "median_log_ret": round(float(np.median(vals)), 6),
            "std": round(float(np.std(vals, ddof=1)), 6),
            "hit_rate_up": round(float(np.mean(vals > 0)), 3),
        }
    return {"horizon": horizon, "by_regime": out}


def multiple_testing_warning(n_tests: int) -> str:
    if n_tests <= 1:
        return "single_test"
    return (
        f"Multiple testing: {n_tests} related tests performed. "
        "Raw p-values / t-stats are not adjusted. Prefer pre-registered hypotheses "
        "and out-of-sample confirmation."
    )


def edge_report(
    signal: Sequence[float],
    close: Sequence[float],
    horizons: Sequence[int] = (1, 3, 6, 12, 24),
    min_samples: int = 40,
) -> Dict[str, Any]:
    """Top-level report used by the decision layer."""
    analysis = signal_forward_analysis(signal, close, horizons=horizons, min_samples=min_samples)
    n_tests = len(horizons)
    analysis["multiple_testing"] = multiple_testing_warning(n_tests)
    analysis["recommendation"] = (
        "NO_TRADE"
        if analysis["evidence_level"] in ("INSUFFICIENT STATISTICAL EVIDENCE", "WEAK_ASSOCIATION")
        else "REVIEW_WITH_COSTS_AND_OOS"
    )
    return analysis
