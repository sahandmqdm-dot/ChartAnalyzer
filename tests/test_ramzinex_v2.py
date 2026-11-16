"""Tests for apply_patch_v2 (Ramzinex corrections). Synthetic data only.  Run: python tests/test_ramzinex_v2.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, pandas as pd
import app


def test_get_fee():
    C = {"fee_roundtrip": 0.004, "fee_by_market": {"tether": 0.001, "rial": 0.004}}
    assert app.get_fee(C, "BTCUSDT") == 0.001 and app.get_fee(C, "ramzinex:BTCUSDT") == 0.001
    assert app.get_fee(C, "USDTIRR") == 0.004 and app.get_fee(C, "BTCIRR") == 0.004   # USDTIRR is a RIAL market
    C["fee_roundtrip"] = 0.007
    assert app.get_fee(C, "weird.csv") == 0.007


def test_proj_param():
    pc = {"warm": {"default": 120, "1d": 100}, "lambda": 0.94}
    assert app.proj_param(pc, "warm", "1d", 1) == 100 and app.proj_param(pc, "warm", "1h", 1) == 120
    assert app.proj_param(pc, "lambda", "4h", 0.5) == 0.94 and app.proj_param(pc, "block", "4h", 5) == 5


def test_rolling_mad_matches_old():
    x = pd.Series(np.random.default_rng(0).normal(size=300).cumsum())
    old = x.rolling(20).apply(lambda a: np.abs(a - a.mean()).mean(), raw=True)
    new = app._rolling_mad(x, 20)
    assert np.allclose(old.to_numpy(), new.to_numpy(), equal_nan=True)


def test_restricted_window_wraps_midnight():
    C = {"restricted_hours": {"enabled": True, "start": "23:30", "end": "11:30", "tz_offset_min": 210, "tf_max_min": 60}}
    # Tehran 12:00 = 08:30 UTC (allowed), Tehran 00:00 = 20:30 UTC (restricted), Tehran 11:30 = 08:00 UTC (allowed)
    day = 86400 * 20000
    t = [day + 8 * 3600 + 1800, day + 20 * 3600 + 1800, day + 8 * 3600, day + 20 * 3600]
    df = pd.DataFrame({"time": t})
    assert list(app.filter_restricted_bars(df, C, "1h")) == [True, False, True, False]  # 11:30 Tehran = allowed (end is exclusive), 23:30 Tehran = restricted (start is inclusive)
    assert app.filter_restricted_bars(df, C, "4h").all()          # longer timeframes are never filtered
    assert app.is_restricted(day + 20 * 3600 + 1800, C, "1h") and not app.is_restricted(day + 8 * 3600 + 1800, C, "1h")


def test_backtest_one_fee_convention():
    r = np.random.default_rng(2)
    c = 100 * np.exp(np.cumsum(r.normal(0.001, 0.01, 600)))
    df = pd.DataFrame({"time": np.arange(600) * 3600.0, "open": c, "high": c * 1.002, "low": c * 0.998, "close": c, "volume": 1.0})
    out = app.backtest(df, "1h", symbol="BTCUSDT")
    expected_fee = app.get_fee(app.conf(), "BTCUSDT")
    expected_spread = app.conf()["backtest"]["spread_slippage_roundtrip"]
    assert abs(out["fee_roundtrip"] - (expected_fee + expected_spread)) < 1e-9
    assert abs(out["fee_per_side"] * 2 - expected_fee) < 1e-9


if __name__ == "__main__":
    for f in (test_get_fee, test_proj_param, test_rolling_mad_matches_old, test_restricted_window_wraps_midnight,
              test_backtest_one_fee_convention):
        f(); print("ok", f.__name__)
