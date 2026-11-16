"""Run:  python -m pytest tests   (or: python tests/test_trend.py).  Synthetic data only - no network, no keys."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np, pandas as pd
import trend_engine as TE


def fake(n=900, seed=3, tsec=86400):
    r = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(r.normal(0.0004, 0.02, n)))
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"time": np.arange(n) * float(tsec), "open": o, "high": np.maximum(o, c) * 1.006,
                         "low": np.minimum(o, c) * 0.994, "close": c, "volume": 1.0})


def test_no_lookahead():
    d = fake(); full = TE.series(d, "1d")
    for k in (300, 500, 800):
        pre = TE.series(d.iloc[:k].reset_index(drop=True), "1d")
        assert abs(pre["score"][-1] - full["score"][k - 1]) < 1e-9
        assert pre["st"]["struct"][-1] == full["st"]["struct"][k - 1]


def test_forming_bar_dropped():
    d = fake(300)
    a = TE.analyse(d, "1d", now=d.time.iloc[-1] + 100)       # last candle still open
    assert a["forming_bar_ignored"] and a["bars_used"] == 299


def test_resample_12h_complete_buckets_only():
    d = fake(600, tsec=14400)
    r = TE.resample_ohlcv(d, 12, 0, now=d.time.iloc[-1] + 10**7)
    assert len(r) == 200 and (np.diff(r.time) == 43200).all()
    assert r.high.iloc[0] == d.high.iloc[:3].max() and r.close.iloc[0] == d.close.iloc[2]


if __name__ == "__main__":
    for f in (test_no_lookahead, test_forming_bar_dropped, test_resample_12h_complete_buckets_only):
        f(); print("ok", f.__name__)
