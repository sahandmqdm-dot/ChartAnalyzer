"""ChartAnalyzer V5 Predictive Market Intelligence (PMI).

Causal, dependency-light diagnostics. No pretrained model or profitability claim.
Probabilities are deliberately withheld until an out-of-sample calibration set exists.
"""
from __future__ import annotations
import math
import numpy as np


def _finite(a):
    a = np.asarray(a, dtype=float)
    return a[np.isfinite(a)]


def _safe_float(x):
    return float(x) if np.isfinite(x) else None


def _log_returns(close):
    c = np.asarray(close, dtype=float)
    if len(c) < 3 or np.any(c <= 0) or not np.all(np.isfinite(c)):
        raise ValueError("close must contain at least 3 finite positive values")
    return np.diff(np.log(c))


def classify_regime(close, high=None, low=None, volume=None):
    """Classify the latest regime from trailing data only; no future bars are used."""
    c = np.asarray(close, float)
    if len(c) < 60 or not np.all(np.isfinite(c)) or np.any(c <= 0):
        raise ValueError("at least 60 valid closes are required")
    r = _log_returns(c)
    w = min(48, len(r))
    recent = r[-w:]
    vol = float(np.std(recent, ddof=1))
    long_vol = float(np.std(r[-min(240, len(r)):], ddof=1))
    vol_ratio = vol / max(long_vol, 1e-12)
    path = float(np.sum(np.abs(recent)))
    efficiency = abs(float(np.sum(recent))) / max(path, 1e-12)
    drift = float(np.sum(recent))
    if vol_ratio > 1.8:
        regime = "volatility_expansion"
    elif vol_ratio < 0.55:
        regime = "volatility_compression"
    elif efficiency > 0.42 and abs(drift) > 0.012:
        regime = "trending_up" if drift > 0 else "trending_down"
    elif efficiency < 0.20:
        regime = "ranging"
    else:
        regime = "transition"
    return {"label": regime, "trend_efficiency": efficiency,
            "volatility_ratio": vol_ratio, "recent_log_return": drift,
            "realized_volatility_per_bar": vol, "method": "causal trailing-window heuristic"}


def _ewma_vol(r, lam=0.94):
    v = float(np.var(r[-min(len(r), 100):]))
    for x in r[-min(len(r), 500):]:
        v = lam * v + (1.0 - lam) * float(x*x)
    return math.sqrt(max(v, 0.0))


def _bootstrap_quantiles(r, horizon, paths=3000, block=4, seed=19):
    """Moving-block bootstrap of demeaned returns; outputs terminal return quantiles."""
    r = np.asarray(r, float)
    pool = r[-min(len(r), 500):]
    if len(pool) < max(30, block * 3):
        raise ValueError("insufficient return history for bootstrap")
    pool = pool - np.median(pool)
    rng = np.random.default_rng(seed)
    nblocks = int(math.ceil(horizon / block))
    starts = rng.integers(0, len(pool)-block+1, size=(paths, nblocks))
    samples = np.empty((paths, nblocks*block), float)
    offsets = np.arange(block)
    for j in range(nblocks):
        samples[:, j*block:(j+1)*block] = pool[starts[:, j, None] + offsets]
    terminal = np.sum(samples[:, :horizon], axis=1)
    return np.expm1(np.quantile(terminal, [0.10, 0.50, 0.90]))


def analyze(df, timeframe="1h", horizons=(1, 4, 12, 24), paths=3000, seed=19):
    """Produce interpretable directional diagnostics and scenario bands.

    Horizons are bars, not hours. This is a scenario distribution, not a point
    prediction. Directional probability is not emitted without calibration.
    """
    required = {"close"}
    if not required.issubset(df.columns):
        raise ValueError("dataframe must include close")
    input_order_status = "UNVERIFIED_NO_TIMESTAMP"
    if "time" in df:
        timestamps = np.asarray(df["time"], dtype=float)
        if not np.isfinite(timestamps).all() or np.any(np.diff(timestamps) <= 0):
            raise ValueError("time values must be finite, unique and strictly increasing")
        input_order_status = "VERIFIED_STRICTLY_INCREASING"
    c = np.asarray(df["close"], float)
    r = _log_returns(c)
    regime = classify_regime(c)
    last = float(c[-1])
    # Momentum blend is a ranking score, not a calibrated probability.
    def mom(n):
        return float(np.log(c[-1] / c[-1-n])) if len(c) > n else 0.0
    m1, m2, m3 = mom(min(6, len(c)-1)), mom(min(24, len(c)-1)), mom(min(48, len(c)-1))
    scale = max(float(np.std(r[-min(100, len(r)):], ddof=1)), 1e-8)
    score = float(np.tanh((0.45*m1 + 0.35*m2 + 0.20*m3) / (scale * math.sqrt(24))))
    # Detect latest close crossing a trailing range formed strictly before latest bar.
    look = min(30, len(c)-1)
    prev_hi = float(np.max(np.asarray(df["high"], float)[-look-1:-1])) if "high" in df else float(np.max(c[-look-1:-1]))
    prev_lo = float(np.min(np.asarray(df["low"], float)[-look-1:-1])) if "low" in df else float(np.min(c[-look-1:-1]))
    breakout = "up" if last > prev_hi else ("down" if last < prev_lo else "none")
    vol = _ewma_vol(r)
    forecast = {}
    for h in horizons:
        h = int(h)
        if h < 1 or h > 300:
            continue
        q = _bootstrap_quantiles(r, h, paths=paths, seed=seed+h)
        forecast[str(h)] = {"horizon_bars": h,
            "return_quantiles": {"p10": _safe_float(q[0]), "p50": _safe_float(q[1]), "p90": _safe_float(q[2])},
            "price_quantiles": {"p10": _safe_float(last*(1+q[0])), "p50": _safe_float(last*(1+q[1])), "p90": _safe_float(last*(1+q[2]))},
            "annualization": "not applied", "distribution": "demeaned moving-block bootstrap"}
    return {"engine": "ChartAnalyzer PMI", "version": "5.0.0", "timeframe": timeframe,
        "last_price": last, "regime": regime, "input_order_status": input_order_status,
        "directional_score": score, "directional_label": "bullish_bias" if score > .12 else ("bearish_bias" if score < -.12 else "neutral"),
        "directional_probability": None,
        "probability_status": "not_calibrated: collect resolved out-of-sample journal predictions before reporting probabilities",
        "breakout": {"state": breakout, "reference_high": prev_hi, "reference_low": prev_lo},
        "volatility_forecast": {"ewma_sigma_per_bar": vol, "method": "EWMA lambda=0.94"},
        "scenario_forecasts": forecast,
        "limitations": ["heuristic directional score is not a probability", "bootstrap assumes historical return blocks remain informative", "fees, spread, slippage and order-book effects are not included", "not a trading instruction"]}


def validation_summary(actual, predicted, probabilities=None):
    """Basic out-of-sample metrics. probabilities must be calibrated predictions in [0,1]."""
    y = np.asarray(actual, float); p = np.asarray(predicted, float)
    if y.shape != p.shape or y.size == 0:
        raise ValueError("actual and predicted must be non-empty arrays of equal shape")
    mask = np.isfinite(y) & np.isfinite(p)
    y, p = y[mask], p[mask]
    out = {"n": int(len(y)), "mae": float(np.mean(np.abs(y-p))),
           "rmse": float(np.sqrt(np.mean((y-p)**2)))}
    if probabilities is not None:
        pr = np.asarray(probabilities, float)
        if pr.shape != mask.shape:
            raise ValueError("probabilities must match input shape")
        pr = pr[mask]
        if np.any((pr < 0) | (pr > 1)):
            raise ValueError("probabilities must lie in [0,1]")
        out["brier"] = float(np.mean((pr-y)**2))
    return out
