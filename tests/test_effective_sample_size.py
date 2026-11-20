import numpy as np
import pytest
from effective_sample_size import hac_effective_sample_size, timestamped_portfolio_ess, trade_level_effective_sample_size

def test_iid_series_has_ess_near_sample_size():
    x = np.random.default_rng(120).normal(size=20000)
    out = hac_effective_sample_size(x, bandwidth=20)
    assert 0.75 < out["ess_ratio"] <= 1.0

def test_positive_ar1_dependence_reduces_ess():
    rng = np.random.default_rng(4); noise = rng.normal(size=12000); x = np.zeros_like(noise)
    for i in range(1, len(x)): x[i] = .85*x[i-1] + noise[i]
    out = hac_effective_sample_size(x, bandwidth=100)
    assert out["ess"] < out["n_observations"] * .5

def test_timestamped_equity_accepts_regular_series():
    times = np.arange(101, dtype=np.int64)*3600
    equity = np.cumprod(1+np.random.default_rng(3).normal(.0001,.001,101))
    out = timestamped_portfolio_ess(times,equity,expected_period_seconds=3600)
    assert out["n_observations"] == 100 and out["missing_periods"] == 0

def test_timestamped_equity_rejects_embargo_gap():
    with pytest.raises(ValueError, match="gaps"):
        timestamped_portfolio_ess([0,3600,7200,14400,18000],[1,1.01,1.02,1.03,1.04])

def test_timestamped_equity_rejects_duplicate_timestamps():
    with pytest.raises(ValueError, match="unique"):
        timestamped_portfolio_ess([0,3600,3600,10800],[1,1.01,1.02,1.03])

def test_trade_ess_uses_net_account_returns_and_rejects_overlap():
    trades=[{"entry_bar":1,"exit_bar":3,"net_ret":.01},{"entry_bar":4,"exit_bar":6,"net_ret":-.005},
            {"entry_bar":7,"exit_bar":9,"net_ret":.02},{"entry_bar":10,"exit_bar":12,"net_ret":.003}]
    out=trade_level_effective_sample_size(trades)
    assert out["return_key"] == "net_ret" and out["trade_count"] == 4
    trades[1]["entry_bar"]=2
    with pytest.raises(ValueError,match="overlapping"): trade_level_effective_sample_size(trades)

@pytest.mark.parametrize("values", [[1,1,1,1],[1,2,np.nan,4],[1,2,np.inf,4]])
def test_constant_or_nonfinite_series_fail_closed(values):
    with pytest.raises(ValueError): hac_effective_sample_size(values)
