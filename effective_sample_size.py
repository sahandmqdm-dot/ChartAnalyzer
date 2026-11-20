"""Dependence-aware effective sample size diagnostics."""
from __future__ import annotations
import math
from typing import Any
import numpy as np

def _finite_vector(values: Any, name: str) -> np.ndarray:
    x = np.asarray(values, dtype=float)
    if x.ndim != 1 or len(x) < 3 or not np.isfinite(x).all():
        raise ValueError(f"{name} must be a finite 1-D series with at least 3 observations")
    return x

def _ess_from_hac(x: np.ndarray, bandwidth: int) -> dict:
    n = len(x)
    centered = x - float(np.mean(x))
    gamma0 = float(np.dot(centered, centered) / n)
    if not math.isfinite(gamma0) or gamma0 <= np.finfo(float).eps:
        raise ValueError("ESS is undefined for a constant or numerically zero-variance series")
    omega = gamma0
    acf = []
    for lag in range(1, bandwidth + 1):
        gamma = float(np.dot(centered[lag:], centered[:-lag]) / n)
        weight = 1.0 - lag / (bandwidth + 1.0)
        omega += 2.0 * weight * gamma
        acf.append(gamma / gamma0)
    if not math.isfinite(omega) or omega <= 0:
        raise ValueError("HAC long-run variance is non-positive or invalid")
    raw = n * gamma0 / omega
    return {"n_observations": n, "ess_raw": float(raw),
            "ess": float(np.clip(raw, 1.0, float(n))),
            "ess_ratio": float(np.clip(raw, 1.0, float(n)) / n),
            "gamma0": gamma0, "long_run_variance": float(omega),
            "bandwidth": int(bandwidth), "kernel": "Bartlett",
            "autocorrelation_estimates": acf,
            "method": "Newey-West HAC long-run variance", "status": "DIAGNOSTIC"}

def hac_effective_sample_size(returns, *, bandwidth: int | None = None,
                              holding_period_bars: int | None = None) -> dict:
    """ESS for equally spaced net portfolio returns, including flat periods."""
    x = _finite_vector(returns, "returns")
    n = len(x)
    rule = max(1, min(int(math.floor(4.0 * (n / 100.0) ** (2.0 / 9.0))), n - 1))
    if holding_period_bars is not None:
        if isinstance(holding_period_bars, bool) or int(holding_period_bars) != holding_period_bars or holding_period_bars < 1:
            raise ValueError("holding_period_bars must be a positive integer")
        rule = max(rule, min(int(holding_period_bars), n - 1))
    selected = rule if bandwidth is None else bandwidth
    if isinstance(selected, bool) or int(selected) != selected or not 0 <= int(selected) < n:
        raise ValueError("bandwidth must be an integer in [0, n_observations)")
    out = _ess_from_hac(x, int(selected))
    out.update({"rule_of_thumb_bandwidth": max(1, min(int(math.floor(4.0 * (n / 100.0) ** (2.0 / 9.0))), n - 1)),
                "holding_period_bars": holding_period_bars,
                "series_semantics": "equally_spaced_net_portfolio_period_returns"})
    return out

def timestamped_portfolio_ess(timestamps, equity, *, bandwidth: int | None = None,
                              expected_period_seconds: float | None = None) -> dict:
    """Compute time-series ESS only for a complete regular equity timeline."""
    t, e = np.asarray(timestamps), np.asarray(equity, dtype=float)
    if t.ndim != 1 or e.ndim != 1 or len(t) != len(e) or len(t) < 4:
        raise ValueError("timestamps and equity must be 1-D, equal length, and contain >=4 points")
    if not np.isfinite(e).all() or np.any(e <= 0):
        raise ValueError("equity must be finite and strictly positive")
    try:
        if np.issubdtype(t.dtype, np.datetime64):
            ticks = t.astype("datetime64[ns]").astype(np.int64).astype(float) / 1e9
        elif np.issubdtype(t.dtype, np.number):
            ticks = t.astype(float)
        else:
            ticks = np.asarray([np.datetime64(v, "ns").astype(np.int64) for v in t], dtype=float) / 1e9
    except (TypeError, ValueError, OverflowError):
        raise ValueError("timestamps must be numeric epoch values or parseable datetimes") from None
    if not np.isfinite(ticks).all() or np.any(np.diff(ticks) <= 0):
        raise ValueError("timestamps must be finite, unique, and strictly increasing")
    deltas = np.diff(ticks)
    period = float(np.median(deltas)) if expected_period_seconds is None else float(expected_period_seconds)
    if not math.isfinite(period) or period <= 0:
        raise ValueError("expected period must be finite and positive")
    if np.any(np.abs(deltas - period) > max(1e-6, abs(period) * 1e-6)):
        raise ValueError("timestamp gaps or irregular periods detected; do not compress missing/embargo bars")
    returns = e[1:] / e[:-1] - 1.0
    out = hac_effective_sample_size(returns, bandwidth=bandwidth)
    out.update({"timestamp_start": str(t[0]), "timestamp_end": str(t[-1]),
                "period_seconds": period, "n_equity_points": len(e),
                "missing_periods": 0,
                "series_semantics": "timestamp_verified_net_portfolio_equity_returns"})
    return out

def trade_level_effective_sample_size(trades, *, return_key: str = "net_ret",
                                      entry_key: str = "entry_bar", exit_key: str = "exit_bar",
                                      bandwidth: int | None = None) -> dict:
    """Diagnostic ESS over completed, chronological, non-overlapping trade returns."""
    if not isinstance(trades, (list, tuple)) or len(trades) < 3:
        raise ValueError("at least 3 completed trade records are required")
    ordered = sorted(trades, key=lambda row: int(row[entry_key]))
    entries = np.asarray([int(row[entry_key]) for row in ordered], dtype=int)
    exits = np.asarray([int(row[exit_key]) for row in ordered], dtype=int)
    if np.any(entries < 0) or np.any(exits <= entries):
        raise ValueError("trade entry/exit bars are invalid")
    if np.any(entries[1:] < exits[:-1]):
        raise ValueError("overlapping trades are not supported")
    values = _finite_vector([row[return_key] for row in ordered], "trade net account returns")
    n = len(values)
    rule = max(1, min(int(math.floor(4.0 * (n / 100.0) ** (2.0 / 9.0))), n - 1))
    selected = rule if bandwidth is None else bandwidth
    if isinstance(selected, bool) or int(selected) != selected or not 0 <= int(selected) < n:
        raise ValueError("bandwidth must be an integer in [0, trade_count)")
    out = _ess_from_hac(values, int(selected))
    out.update({"trade_count": n, "return_key": return_key,
                "first_entry_bar": int(entries[0]), "last_exit_bar": int(exits[-1]),
                "median_holding_bars": float(np.median(exits - entries)),
                "series_semantics": "chronological_non_overlapping_net_trade_account_returns",
                "warning": "trade-level diagnostic only; not portfolio time-series ESS"})
    return out

__all__ = ["hac_effective_sample_size", "timestamped_portfolio_ess", "trade_level_effective_sample_size"]
