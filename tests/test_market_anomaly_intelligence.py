import numpy as np
import pandas as pd
import pytest

from market_anomaly_intelligence import (
    analyze_market_anomalies,
    summarize_latest_anomalies,
)


def make_candles(n=180, seed=7):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    open_price = np.r_[close[0], close[:-1]]
    spread = close * 0.001
    return pd.DataFrame({
        "time": np.arange(n, dtype=float) * 3600,
        "open": open_price,
        "high": np.maximum(open_price, close) + spread,
        "low": np.minimum(open_price, close) - spread,
        "close": close,
        "volume": rng.lognormal(5, 0.15, n),
    })


def test_output_is_causal_and_first_baseline_rows_are_unavailable():
    candles = make_candles()
    result = analyze_market_anomalies(candles, window=40)
    assert result.iloc[:40]["state"].eq("INSUFFICIENT_BASELINE").all()
    changed_future = candles.copy()
    changed_future.loc[150:, "close"] *= 2
    changed_future.loc[150:, "high"] *= 2
    changed_future.loc[150:, "low"] *= 2
    revised = analyze_market_anomalies(changed_future, window=40)
    pd.testing.assert_frame_equal(result.iloc[:150], revised.iloc[:150])


def test_large_price_range_and_volume_event_is_detected():
    candles = make_candles()
    i = len(candles) - 1
    candles.loc[i, "close"] = candles.loc[i, "open"] * 1.15
    candles.loc[i, "high"] = candles.loc[i, "close"] * 1.01
    candles.loc[i, "low"] = candles.loc[i, "open"] * 0.99
    candles.loc[i, "volume"] *= 30
    result = analyze_market_anomalies(candles, window=60)
    assert result.iloc[-1]["state"] == "ANOMALY_DETECTED"
    assert result.iloc[-1]["anomaly_count"] >= 2
    assert "return_shock" in result.iloc[-1]["anomaly_types"]
    assert "volume_spike" in result.iloc[-1]["anomaly_types"]


def test_summary_is_explicitly_non_actionable():
    result = summarize_latest_anomalies(analyze_market_anomalies(make_candles()))
    assert result["status"] == "RESEARCH_ONLY"
    assert result["actionable"] is False
    assert "probability" not in result


@pytest.mark.parametrize("bad", [np.nan, np.inf, -1])
def test_invalid_volume_is_rejected(bad):
    candles = make_candles()
    candles.loc[len(candles) - 1, "volume"] = bad
    with pytest.raises(ValueError):
        analyze_market_anomalies(candles)


def test_impossible_ohlc_and_non_monotonic_time_are_rejected():
    candles = make_candles()
    candles.loc[10, "high"] = candles.loc[10, "low"] - 1
    with pytest.raises(ValueError, match="geometry"):
        analyze_market_anomalies(candles)
    candles = make_candles()
    candles.loc[10, "time"] = candles.loc[9, "time"]
    with pytest.raises(ValueError, match="strictly increasing"):
        analyze_market_anomalies(candles)


def test_score_is_not_reported_as_probability():
    result = analyze_market_anomalies(make_candles(), window=30)
    assert result.attrs["score_semantics"].startswith("relative anomaly")
    assert result["event_score"].dropna().between(0, 100).all()
