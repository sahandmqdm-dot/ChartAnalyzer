"""
ChartAnalyzer V6 - Future Prediction Engine (core)
=================================================
Multi-horizon, multi-method forecasting with explicit uncertainty.

Methods included (causal, dependency-light):
- Regime-aware moving-block bootstrap (primary distribution)
- EWMA volatility projection
- Simple drift estimation only when statistically supported
- Directional scenario probabilities are EXPERIMENTAL until calibrated

Never presents a single deterministic price as guaranteed.
Never uses future data.
"""
from __future__ import annotations
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple
import numpy as np

from statistical_edge import effective_sample_size, pearson_corr


def _finite(a):
    a = np.asarray(a, dtype=float)
    return a[np.isfinite(a)]


def _log_returns(close):
    c = np.asarray(close, dtype=float)
    if len(c) < 5 or np.any(c <= 0) or not np.all(np.isfinite(c)):
        raise ValueError("close must contain finite positive values")
    return np.diff(np.log(c))


def _ewma_vol(r, lam=0.94):
    r = np.asarray(r, dtype=float)
    if len(r) < 5:
        return float(np.std(r)) if len(r) else 0.0
    v = float(np.var(r[-min(len(r), 80):]))
    for x in r[-min(len(r), 400):]:
        v = lam * v + (1.0 - lam) * float(x * x)
    return math.sqrt(max(v, 1e-16))


def block_bootstrap_paths(
    returns: np.ndarray,
    horizon: int,
    n_paths: int = 2000,
    block: int = 5,
    seed: int = 42,
    demean: bool = True,
) -> np.ndarray:
    """Generate terminal cumulative returns via moving-block bootstrap."""
    r = np.asarray(returns, dtype=float)
    pool = r[-min(len(r), 600):]
    if len(pool) < max(20, block * 2):
        raise ValueError("insufficient history for bootstrap")
    if demean:
        pool = pool - np.median(pool)
    rng = np.random.default_rng(seed)
    n_blocks = int(math.ceil(horizon / block))
    # Pre-sample starting indices
    starts = rng.integers(0, len(pool) - block + 1, size=(n_paths, n_blocks))
    paths = np.empty(n_paths, dtype=float)
    for i in range(n_paths):
        segs = []
        for b in range(n_blocks):
            s = starts[i, b]
            segs.append(pool[s : s + block])
        path = np.concatenate(segs)[:horizon]
        paths[i] = float(np.sum(path))
    return paths


def quantiles_from_paths(paths: np.ndarray, qs=(0.10, 0.25, 0.50, 0.75, 0.90)) -> Dict[str, float]:
    qv = np.quantile(paths, qs)
    return {f"p{int(q*100):02d}": float(v) for q, v in zip(qs, qv)}


def estimate_drift(r: np.ndarray, min_t: float = 2.0) -> Tuple[float, str]:
    """Return (drift_per_bar, status). Drift used only if t-stat supports it."""
    r = _finite(r)
    if len(r) < 30:
        return 0.0, "INSUFFICIENT"
    mu = float(np.mean(r))
    se = float(np.std(r, ddof=1)) / math.sqrt(len(r))
    if se < 1e-12:
        return 0.0, "ZERO_VAR"
    t = mu / se
    if abs(t) >= min_t:
        return mu, f"SUPPORTED_t={t:.2f}"
    return 0.0, f"NOT_SUPPORTED_t={t:.2f}"


def multi_horizon_forecast(
    close: Sequence[float],
    horizons: Sequence[int] = (1, 3, 6, 12, 24, 48),
    n_paths: int = 2500,
    seed: int = 42,
    block: int = 4,
) -> Dict[str, Any]:
    """
    Core multi-horizon distributional forecast.
    Prices are obtained by exp(log_return) scaling of last close.
    """
    c = np.asarray(close, dtype=float)
    if len(c) < 60:
        raise ValueError("need at least 60 closes")
    last = float(c[-1])
    r = _log_returns(c)
    vol = _ewma_vol(r)
    drift, drift_status = estimate_drift(r[-min(len(r), 200):])

    forecasts = {}
    for h in horizons:
        h = int(h)
        if h < 1 or h > 500:
            continue
        paths = block_bootstrap_paths(r, h, n_paths=n_paths, block=block, seed=seed + h)
        # Optional small drift tilt only when supported
        if drift_status.startswith("SUPPORTED"):
            paths = paths + drift * h
        q = quantiles_from_paths(paths)
        price_q = {k: last * math.exp(v) for k, v in q.items()}
        med_ret = q["p50"]
        forecasts[str(h)] = {
            "horizon_bars": h,
            "return_quantiles": {k: round(v, 6) for k, v in q.items()},
            "price_quantiles": {k: round(v, 6) for k, v in price_q.items()},
            "median_price": round(price_q["p50"], 6),
            "expected_return_pct": round(100 * (math.exp(med_ret) - 1), 4),
            "vol_ewma_per_bar": round(vol, 6),
            "projected_vol_horizon": round(vol * math.sqrt(h), 6),
            "drift_used": drift_status,
            "method": "regime-agnostic moving-block bootstrap (+ drift only if t-supported)",
        }

    # Experimental directional probabilities from bootstrap mass
    # These are NOT calibrated real-world probabilities.
    dir_probs = {}
    for h, f in forecasts.items():
        # Use path quantiles roughly; better would store full paths
        # Approximate P(up) from median and dispersion
        p50 = f["return_quantiles"]["p50"]
        p10 = f["return_quantiles"]["p10"]
        p90 = f["return_quantiles"]["p90"]
        # Crude: assume roughly normal for illustration only
        sigma = (p90 - p10) / 2.56
        if sigma < 1e-9:
            pup = 0.5
        else:
            # P(R>0)
            from math import erf, sqrt
            z = -p50 / (sigma + 1e-12)
            pup = 0.5 * (1 - erf(z / sqrt(2)))
        pdown = 1 - pup
        # Sideways band ~ +/- 0.5 * projected vol
        band = 0.5 * f["projected_vol_horizon"]
        # Re-estimate with band (still experimental)
        dir_probs[h] = {
            "p_bullish": round(float(pup), 3),
            "p_bearish": round(float(pdown), 3),
            "p_sideways_approx": None,
            "status": "EXPERIMENTAL_UNCALIBRATED",
            "note": "Bootstrap frequency is not real-world probability. Calibrate via Forecast Journal before use.",
        }

    return {
        "engine": "ChartAnalyzer ForecastEngine",
        "version": "6.0.0",
        "last_price": last,
        "n_history": len(c),
        "horizons": forecasts,
        "directional_probabilities": dir_probs,
        "volatility": {"ewma_per_bar": vol, "method": "EWMA λ=0.94"},
        "drift_status": drift_status,
        "limitations": [
            "Bootstrap assumes historical return blocks remain informative",
            "Directional probabilities are experimental until journal calibration",
            "No fees, spread or liquidity modelled inside the cone",
            "Not a trading instruction",
        ],
    }


def scenario_narratives(forecast: Dict[str, Any], structure: Optional[Dict] = None) -> Dict[str, Any]:
    """Generate three scenario skeletons from forecast quantiles (no fiction)."""
    if not forecast.get("horizons"):
        return {}
    # Use a medium horizon if available
    keys = sorted(forecast["horizons"].keys(), key=lambda x: int(x))
    mid = keys[len(keys) // 2]
    f = forecast["horizons"][mid]
    last = forecast["last_price"]
    p10, p50, p90 = f["price_quantiles"]["p10"], f["price_quantiles"]["p50"], f["price_quantiles"]["p90"]
    return {
        "horizon_used": mid,
        "bullish": {
            "target_zone": [round(p50, 4), round(p90, 4)],
            "invalidation_approx": round(p10, 4),
            "conditions": "Price acceptance above recent structure highs with volume confirmation",
            "probability_status": "see directional_probabilities (experimental)",
        },
        "bearish": {
            "target_zone": [round(p10, 4), round(p50, 4)],
            "invalidation_approx": round(p90, 4),
            "conditions": "Price acceptance below recent structure lows",
            "probability_status": "see directional_probabilities (experimental)",
        },
        "sideways": {
            "range_approx": [round(min(p10, last * 0.99), 4), round(max(p90, last * 1.01), 4)],
            "conditions": "Volatility compression and failed breakouts",
            "probability_status": "experimental",
        },
    }
