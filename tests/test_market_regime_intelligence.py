import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from market_regime_intelligence import analyze_market_regimes, summarize_regime_state


def _candles(n=700, seed=21):
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0002, 0.008, n)
    close = 100 * np.exp(np.cumsum(returns))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + rng.uniform(0.0001, 0.004, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0.0001, 0.004, n))
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": rng.uniform(10, 100, n)})


def test_output_is_aligned_and_uses_only_known_regime_labels():
    d = _candles()
    r = analyze_market_regimes(d)
    assert len(r) == len(d)
    assert r.index.equals(d.index)
    assert set(r.regime.unique()).issubset({"trending", "ranging", "volatile", "compression", "transition"})
    assert r.transition.dtype == bool


def test_no_lookahead_prefix_invariance():
    d = _candles()
    full = analyze_market_regimes(d)
    for cut in (250, 420, 610):
        prefix = analyze_market_regimes(d.iloc[:cut].copy())
        assert prefix.regime.iloc[-1] == full.regime.iloc[cut - 1]
        assert prefix.transition.iloc[-1] == full.transition.iloc[cut - 1]
        for col in ("adx", "efficiency_ratio", "atr_pct_rank", "bb_width_rank"):
            a, b = prefix[col].iloc[-1], full[col].iloc[cut - 1]
            assert (pd.isna(a) and pd.isna(b)) or abs(float(a) - float(b)) < 1e-10


def test_future_extreme_does_not_change_prior_classification():
    d = _candles()
    base = analyze_market_regimes(d.iloc[:500])
    altered = d.copy()
    altered.loc[500:, ["high", "low", "close"]] *= 5
    changed = analyze_market_regimes(altered)
    assert base.regime.iloc[-1] == changed.regime.iloc[499]
    assert base.atr_pct_rank.iloc[-1] == pytest.approx(changed.atr_pct_rank.iloc[499], nan_ok=True)


def test_bad_ohlc_and_parameters_fail_closed():
    d = _candles(100)
    with pytest.raises(ValueError):
        analyze_market_regimes(d.assign(close=-1))
    with pytest.raises(ValueError):
        analyze_market_regimes(d, lookback=10)
    with pytest.raises(ValueError):
        analyze_market_regimes(d.drop(columns="high"))


def test_summary_is_explicitly_research_only():
    report = summarize_regime_state(analyze_market_regimes(_candles()))
    assert report["status"] == "RESEARCH_ONLY"
    assert "not a trade signal" in report["guardrail"]
