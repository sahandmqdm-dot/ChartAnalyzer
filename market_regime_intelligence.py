"""Causal Market Regime Intelligence for ChartAnalyzer V6.9.2.

Research metadata only. It does not modify the frozen signal, regime weights,
entry thresholds, or execution rules. Every feature at bar t uses data through t.
"""
from __future__ import annotations

import math
from typing import Any, Dict

import numpy as np
import pandas as pd

ENGINE_VERSION = "MarketRegimeIntelligence V1.0"
PRIMARY_REGIMES = ("trending", "ranging", "volatile", "compression", "transition")


def _rolling_percentile(series: pd.Series, window: int, min_periods: int) -> pd.Series:
    """Percentile rank of the current observation in its trailing window."""
    def rank_last(values: np.ndarray) -> float:
        last = values[-1]
        return float(np.mean(values <= last))
    return series.rolling(window=window, min_periods=min_periods).apply(rank_last, raw=True)


def analyze_market_regimes(
    candles: pd.DataFrame,
    *,
    lookback: int = 100,
    atr_period: int = 14,
    er_period: int = 20,
    adx_period: int = 14,
    adx_trend: float = 22.0,
    adx_range: float = 18.0,
    er_trend: float = 0.28,
    er_range: float = 0.22,
) -> pd.DataFrame:
    """Return causal per-bar regime labels, transition events, and diagnostics.

    Regime thresholds are transparent heuristics, not fitted parameters. The
    caller must validate their usefulness on chronological out-of-sample data.
    """
    if not isinstance(candles, pd.DataFrame):
        raise TypeError("candles must be a pandas DataFrame")
    required = {"high", "low", "close"}
    missing = required.difference(candles.columns)
    if missing:
        raise ValueError(f"missing OHLC columns: {', '.join(sorted(missing))}")
    if lookback < 30 or min(atr_period, er_period, adx_period) < 2:
        raise ValueError("lookback must be >= 30 and indicator periods >= 2")
    if not (0 <= er_range < er_trend <= 1):
        raise ValueError("ER thresholds must satisfy 0 <= er_range < er_trend <= 1")
    if not (0 < adx_range < adx_trend):
        raise ValueError("ADX thresholds must satisfy 0 < adx_range < adx_trend")

    high = pd.to_numeric(candles["high"], errors="coerce").astype(float)
    low = pd.to_numeric(candles["low"], errors="coerce").astype(float)
    close = pd.to_numeric(candles["close"], errors="coerce").astype(float)
    if len(candles) == 0:
        raise ValueError("candles must not be empty")
    if not (np.isfinite(high).all() and np.isfinite(low).all() and np.isfinite(close).all()):
        raise ValueError("OHLC values must be finite")
    if (close <= 0).any() or (high <= 0).any() or (low <= 0).any() or (high < low).any():
        raise ValueError("OHLC prices must be positive and high >= low")

    prev_close = close.shift(1)
    tr = pd.concat([(high - low), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / atr_period, adjust=False, min_periods=atr_period).mean()
    atr_pct = (atr / close).replace([np.inf, -np.inf], np.nan)

    up = high.diff()
    down = -low.diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / adx_period, adjust=False, min_periods=adx_period).mean() / atr.replace(0, np.nan)
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / adx_period, adjust=False, min_periods=adx_period).mean() / atr.replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1.0 / adx_period, adjust=False, min_periods=adx_period).mean()

    net_move = (close - close.shift(er_period)).abs()
    path_move = close.diff().abs().rolling(er_period, min_periods=er_period).sum()
    efficiency = (net_move / path_move.replace(0, np.nan)).clip(0, 1)
    mid = close.rolling(20, min_periods=20).mean()
    sd = close.rolling(20, min_periods=20).std(ddof=0)
    bb_width = (4.0 * sd / mid.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)

    minp = max(20, lookback // 2)
    atr_pct_rank = _rolling_percentile(atr_pct, lookback, minp)
    bb_width_rank = _rolling_percentile(bb_width, lookback, minp)

    # The priority is deliberate: expansion volatility, then compression,
    # then directional/trend and range evidence. Ambiguous observations remain
    # transition rather than being forced into a confident class.
    volatile = (atr_pct_rank >= 0.80) | ((bb_width_rank >= 0.85) & (atr_pct_rank >= 0.65))
    compression = (bb_width_rank <= 0.20) & (atr_pct_rank <= 0.35)
    trending = (adx >= adx_trend) & (efficiency >= er_trend)
    ranging = (adx <= adx_range) & (efficiency <= er_range)

    regime = pd.Series("transition", index=candles.index, dtype="object")
    regime.loc[ranging] = "ranging"
    regime.loc[trending] = "trending"
    regime.loc[compression] = "compression"
    regime.loc[volatile] = "volatile"
    regime = regime.where(atr_pct_rank.notna() & bb_width_rank.notna() & adx.notna() & efficiency.notna(), "transition")

    previous = regime.shift(1)
    changed = previous.notna() & regime.ne(previous)
    from_values = previous.where(changed, None)
    to_values = regime.where(changed, None)

    out = pd.DataFrame({
        "regime": regime,
        "transition": changed.astype(bool),
        "transition_from": from_values,
        "transition_to": to_values,
        "adx": adx,
        "efficiency_ratio": efficiency,
        "atr_pct": atr_pct * 100.0,
        "atr_pct_rank": atr_pct_rank,
        "bb_width_pct": bb_width * 100.0,
        "bb_width_rank": bb_width_rank,
    }, index=candles.index)
    out.attrs["engine"] = ENGINE_VERSION
    out.attrs["method"] = "causal_trailing_features_and_rolling_percentile_heuristics"
    out.attrs["research_only"] = True
    return out


def summarize_regime_state(frame: pd.DataFrame) -> Dict[str, Any]:
    """Compact current-state payload suitable for API/dashboard output."""
    if frame is None or frame.empty:
        return {"engine": ENGINE_VERSION, "status": "INSUFFICIENT_DATA", "regime": "unknown"}
    row = frame.iloc[-1]
    def safe(value, digits=4):
        try:
            value = float(value)
            return round(value, digits) if math.isfinite(value) else None
        except (TypeError, ValueError):
            return None
    return {
        "engine": ENGINE_VERSION,
        "status": "RESEARCH_ONLY",
        "regime": str(row["regime"]),
        "transition": bool(row["transition"]),
        "transition_from": None if pd.isna(row["transition_from"]) else str(row["transition_from"]),
        "transition_to": None if pd.isna(row["transition_to"]) else str(row["transition_to"]),
        "adx": safe(row["adx"], 2),
        "efficiency_ratio": safe(row["efficiency_ratio"], 3),
        "atr_pct": safe(row["atr_pct"], 4),
        "atr_pct_rank": safe(row["atr_pct_rank"], 3),
        "bb_width_rank": safe(row["bb_width_rank"], 3),
        "guardrail": "Descriptive regime label only; not a trade signal or proof of predictive edge.",
    }


__all__ = ["analyze_market_regimes", "summarize_regime_state", "ENGINE_VERSION", "PRIMARY_REGIMES"]
