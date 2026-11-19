"""Causal, explainable market-event and anomaly diagnostics.

Anomaly scores measure deviation from a trailing baseline. They are not
probabilities, forecasts, trade signals, or proof of manipulation.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

ENGINE_VERSION = "MarketAnomalyIntelligence V1.0"
_REQUIRED = ("open", "high", "low", "close", "volume")


def _robust_zscore(series: pd.Series, window: int, min_periods: int) -> pd.Series:
    """Current value versus a strictly prior rolling median/MAD baseline."""
    prior = series.shift(1)
    rolling = prior.rolling(window=window, min_periods=min_periods)
    median = rolling.median()
    mad = rolling.apply(
        lambda values: float(np.median(np.abs(values - np.median(values)))),
        raw=True,
    )
    scale = 1.4826 * mad
    fallback = rolling.std(ddof=1)
    scale = scale.where(scale > 1e-12, fallback)
    # A constant trailing baseline is valid information, not missing data.
    # The small floor keeps an unchanged value at z=0 and makes a true deviation visible.
    scale = scale.where(scale > 1e-12, 1e-8)
    return ((series - median) / scale).replace([np.inf, -np.inf], np.nan)


def analyze_market_anomalies(
    candles: pd.DataFrame,
    *,
    window: int = 60,
    min_periods: int | None = None,
    robust_z_threshold: float = 3.5,
) -> pd.DataFrame:
    """Return per-bar anomaly features using no observations after the bar."""
    if not isinstance(candles, pd.DataFrame):
        raise TypeError("candles must be a pandas DataFrame")
    missing = [column for column in _REQUIRED if column not in candles.columns]
    if missing:
        raise ValueError(f"missing required OHLCV columns: {', '.join(missing)}")
    if window < 20:
        raise ValueError("window must be >= 20")
    minimum = window if min_periods is None else int(min_periods)
    if not 10 <= minimum <= window:
        raise ValueError("min_periods must be between 10 and window")
    threshold = float(robust_z_threshold)
    if not math.isfinite(threshold) or threshold < 2:
        raise ValueError("robust_z_threshold must be finite and >= 2")
    if candles.empty:
        raise ValueError("candles must not be empty")

    frame = candles.copy()
    for column in _REQUIRED:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    values = frame.loc[:, _REQUIRED].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("OHLCV values must be finite")
    if (frame[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("OHLC prices must be positive")
    if (frame["volume"] < 0).any():
        raise ValueError("volume cannot be negative")
    geometry = (
        (frame["high"] >= frame[["open", "close", "low"]].max(axis=1))
        & (frame["low"] <= frame[["open", "close", "high"]].min(axis=1))
    )
    if not geometry.all():
        raise ValueError("impossible OHLC geometry")
    if "time" in frame:
        timestamps = pd.to_numeric(frame["time"], errors="coerce").to_numpy(dtype=float)
        if not np.isfinite(timestamps).all() or (np.diff(timestamps) <= 0).any():
            raise ValueError("time values must be finite, unique and strictly increasing")

    previous_close = frame["close"].shift(1)
    log_return_abs = np.log(frame["close"] / previous_close).abs()
    range_pct = (frame["high"] - frame["low"]) / frame["close"]
    log_volume = np.log1p(frame["volume"])
    gap_abs = (frame["open"] / previous_close - 1.0).abs()

    features = pd.DataFrame(index=frame.index)
    features["return_shock_z"] = _robust_zscore(log_return_abs, window, minimum)
    features["range_expansion_z"] = _robust_zscore(range_pct, window, minimum)
    features["volume_surprise_z"] = _robust_zscore(log_volume, window, minimum)
    features["gap_shock_z"] = _robust_zscore(gap_abs, window, minimum)

    ready = features.notna().all(axis=1)
    ret = features["return_shock_z"].abs()
    range_z = features["range_expansion_z"]
    volume_z = features["volume_surprise_z"]
    gap_z = features["gap_shock_z"]

    flags = pd.DataFrame(index=frame.index)
    flags["return_shock"] = ret >= threshold
    flags["range_expansion"] = range_z >= threshold
    flags["volume_spike"] = volume_z >= threshold
    flags["volume_drought"] = volume_z <= -threshold
    flags["gap_event"] = gap_z >= threshold
    flags["price_volume_dislocation"] = (ret >= threshold) & (volume_z.abs() < 1.0)
    flags = flags.fillna(False).astype(bool)

    weights = {
        "return_shock_z": 0.35,
        "range_expansion_z": 0.25,
        "volume_surprise_z": 0.25,
        "gap_shock_z": 0.15,
    }
    normalized = sum(
        weight * features[column].abs().clip(upper=2 * threshold) / (2 * threshold)
        for column, weight in weights.items()
    )
    features["event_score"] = (normalized * 100.0).clip(0, 100).where(ready)
    features["anomaly_count"] = flags.sum(axis=1).astype(int)
    features["anomaly_types"] = flags.apply(
        lambda row: "|".join(name for name, active in row.items() if active), axis=1
    )
    features["state"] = np.where(
        ~ready, "INSUFFICIENT_BASELINE",
        np.where(features["anomaly_count"] > 0, "ANOMALY_DETECTED", "NORMAL"),
    )
    features["baseline_ready"] = ready
    features.attrs["engine"] = ENGINE_VERSION
    features.attrs["method"] = "prior_only_rolling_median_MAD_with_std_fallback"
    features.attrs["research_only"] = True
    features.attrs["score_semantics"] = "relative anomaly severity score; not a probability"
    return features


def summarize_latest_anomalies(frame: pd.DataFrame) -> dict[str, Any]:
    """Compact, explainable latest-bar anomaly summary."""
    if frame is None or frame.empty:
        return {"engine": ENGINE_VERSION, "status": "INSUFFICIENT_DATA", "state": "UNKNOWN"}
    row = frame.iloc[-1]
    state = str(row.get("state", "UNKNOWN"))
    def safe(value: Any) -> float | None:
        try:
            number = float(value)
            return round(number, 4) if math.isfinite(number) else None
        except (TypeError, ValueError):
            return None
    return {
        "engine": ENGINE_VERSION,
        "status": "RESEARCH_ONLY",
        "state": state,
        "event_score": safe(row.get("event_score")),
        "anomaly_count": int(row.get("anomaly_count", 0)),
        "anomaly_types": [name for name in str(row.get("anomaly_types", "")).split("|") if name],
        "evidence": {
            "return_shock_z": safe(row.get("return_shock_z")),
            "range_expansion_z": safe(row.get("range_expansion_z")),
            "volume_surprise_z": safe(row.get("volume_surprise_z")),
            "gap_shock_z": safe(row.get("gap_shock_z")),
        },
        "guardrail": "A statistical anomaly is not proof of manipulation, direction, or a trading opportunity.",
        "actionable": False,
    }


__all__ = ["ENGINE_VERSION", "analyze_market_anomalies", "summarize_latest_anomalies"]
