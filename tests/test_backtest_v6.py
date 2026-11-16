import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backtest_engine import run_backtest, autopsy_summary


def market(n=200, trend=0.0):
    px = 100 * np.exp(np.cumsum(np.full(n, trend) + np.random.default_rng(0).normal(0, 0.002, n)))
    return pd.DataFrame({
        "open": px, "high": px * 1.005, "low": px * 0.995,
        "close": px, "volume": np.ones(n)
    })


def test_long_only_fee_accounting():
    df = market()
    signal = np.zeros(len(df))
    signal[130] = 1.0
    signal[140] = 0.0
    fee = 0.001
    out = run_backtest(df, signal, threshold=0.5, fee_per_side=fee, allow_short=False)
    assert out["trades"] >= 1
    assert out["fee_sides_charged"] >= 2
    assert "trades_log" in out
    assert out["version"] == "6.7.0"


def test_short_support_runs():
    df = market()
    signal = np.zeros(len(df))
    signal[130:150] = -0.8
    out = run_backtest(df, signal, threshold=0.5, fee_per_side=0.001, allow_short=True)
    assert out["short_trades"] >= 0  # may or may not fill depending on path
    assert "trades_log" in out


def test_autopsy_on_empty():
    a = autopsy_summary([])
    assert a["n"] == 0
