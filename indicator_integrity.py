"""ChartAnalyzer V6.5 indicator integrity and metric semantics.

Audits indicator outputs without silently converting missing latest values into
tradeable neutral signals. It does not tune parameters or infer profitability.
"""
from __future__ import annotations
import math
from typing import Any, Dict
import numpy as np

# The displayed Value column is NOT always a price level. Keep these semantics
# explicit so a distance/spread is never mistaken for the underlying price.
METRIC_META = {
    "ema": ("price_distance", "fast EMA minus slow EMA", "quote-currency units"),
    "sma": ("price_distance", "close minus SMA", "quote-currency units"),
    "macd": ("price_distance", "MACD histogram", "quote-currency units"),
    "adx": ("strength", "ADX trend strength", "0-100"),
    "ichimoku": ("price_distance", "close distance outside the Ichimoku cloud", "quote-currency units"),
    "supertrend": ("price_level", "SuperTrend trailing level", "quote-currency units"),
    "rsi": ("oscillator", "Relative Strength Index", "0-100"),
    "stoch": ("oscillator", "Stochastic %K", "0-100"),
    "cci": ("oscillator", "Commodity Channel Index", "index points"),
    "willr": ("oscillator", "Williams %R", "-100 to 0"),
    "bb": ("oscillator", "Bollinger %B", "ratio"),
    "obv": ("volume_distance", "OBV minus its EMA", "base-volume units"),
    "mfi": ("oscillator", "Money Flow Index", "0-100"),
    "cmf": ("oscillator", "Chaikin Money Flow", "-1 to 1"),
    "vwap": ("price_distance", "close minus rolling VWAP", "quote-currency units"),
    "donchian": ("normalized_position", "position within prior Donchian channel", "-1 to 1"),
    "keltner": ("normalized_position", "ATR-normalized distance from Keltner midline", "ATR units"),
}

WARMUP_BARS = {
    "ema": 0, "sma": 199, "macd": 0, "adx": 28, "ichimoku": 77,
    "supertrend": 10, "rsi": 14, "stoch": 16, "cci": 19,
    "willr": 13, "bb": 19, "obv": 20, "mfi": 14, "cmf": 19,
    "vwap": 99, "donchian": 20, "keltner": 20,
}

RANGES = {
    "adx": (0.0, 100.0), "rsi": (0.0, 100.0), "stoch": (0.0, 100.0),
    "willr": (-100.0, 0.0), "bb": (-5.0, 6.0), "mfi": (0.0, 100.0),
    "cmf": (-1.000001, 1.000001), "donchian": (-1.000001, 1.000001),
    "keltner": (-1.000001, 1.000001),
}


def _finite(x: Any) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError, OverflowError):
        return False


def audit_indicators(result: Dict[str, Any], close, *, max_price_distance_ratio: float = 1.0) -> Dict[str, Any]:
    """Audit the latest computed indicator point and report historical coverage."""
    close_s = np.asarray(close, dtype=float)
    last_close = float(close_s[-1]) if len(close_s) else float("nan")
    errors, warnings, rows = [], [], []
    members = result.get("members", {})
    enabled = [k for group in members.values() for k in group]
    for key in enabled:
        values = result.get("val", {}).get(key)
        signals = result.get("sig", {}).get(key)
        if values is None or signals is None:
            errors.append(f"{key}: output series missing")
            continue
        v = np.asarray(values, dtype=float)
        s = np.asarray(signals, dtype=float)
        valid_v = np.isfinite(v)
        valid_s = np.isfinite(s)
        latest_valid = bool(len(v) and valid_v[-1] and len(s) and valid_s[-1])
        warmup = WARMUP_BARS.get(key, 0)
        post_warmup = valid_v[min(warmup, len(valid_v)):]
        coverage = float(post_warmup.mean()) if len(post_warmup) else 0.0
        signal_ok = bool(valid_s.all() and (np.abs(s[valid_s]) <= 1.000001).all())
        if not latest_valid:
            errors.append(f"{key}: latest indicator value is unavailable; neutral fill must not be trusted")
        if not signal_ok:
            errors.append(f"{key}: signal contains non-finite or out-of-range values")
        if len(post_warmup) and coverage < 0.98:
            warnings.append(f"{key}: valid coverage after its {warmup}-bar warm-up is only {coverage:.1%}")
        kind, meaning, unit = METRIC_META.get(key, ("unknown", "indicator-specific metric", "unknown"))
        latest = float(v[-1]) if len(v) and valid_v[-1] else None
        if latest is not None and key in RANGES:
            lo, hi = RANGES[key]
            if not lo <= latest <= hi:
                errors.append(f"{key}: latest value {latest:g} outside expected range [{lo:g}, {hi:g}]")
        if latest is not None and kind == "price_level" and _finite(last_close) and last_close > 0:
            ratio = latest / last_close
            if not 0.5 <= ratio <= 1.5:
                errors.append(f"{key}: price-level output is {ratio:.3g}x close; possible price-scale mismatch")
        if latest is not None and kind == "price_distance" and _finite(last_close) and last_close > 0:
            ratio = abs(latest) / last_close
            if ratio > max_price_distance_ratio:
                warnings.append(f"{key}: distance is {ratio:.1%} of close; verify source units and market scale")
        rows.append({"key": key, "metric_kind": kind, "meaning": meaning, "unit": unit,
                     "latest_value": latest, "warmup_bars": warmup,
                     "valid_coverage_pct": round(coverage * 100, 2),
                     "latest_valid": latest_valid, "signal_valid": signal_ok})
    state = "BLOCKED" if errors else "WARN" if warnings else "PASS"
    return {"state": state, "valid": not errors, "enabled_indicators": len(enabled),
            "checked_indicators": len(rows), "latest_close": last_close if _finite(last_close) else None,
            "errors": errors, "warnings": warnings, "indicators": rows,
            "policy": "Missing latest indicator values are blocking; no missing value is silently treated as a valid neutral signal."}


__all__ = ["audit_indicators", "METRIC_META"]
