import numpy as np
import pytest

from advanced_overfit_diagnostics import deflated_sharpe_ratio, probability_of_backtest_overfitting

def test_dsr_reports_more_trials_as_stricter_threshold():
    returns = np.array([0.012, -0.004, 0.009, 0.003, -0.002, 0.011, 0.006, -0.003,
                        0.008, 0.004, -0.001, 0.010, 0.002, -0.004, 0.009, 0.005])
    one = deflated_sharpe_ratio(returns, n_trials=1)
    many = deflated_sharpe_ratio(returns, n_trials=100)
    assert many["expected_max_sharpe_threshold"] > one["expected_max_sharpe_threshold"]
    assert 0 <= many["deflated_sharpe_probability"] <= 1
    assert many["observations"] == len(returns)

def test_dsr_rejects_invalid_trial_count_and_constant_returns():
    with pytest.raises(ValueError):
        deflated_sharpe_ratio([1, 2, 3], n_trials=0)
    with pytest.raises(ValueError):
        deflated_sharpe_ratio([1, 1, 1, 1], n_trials=4)

def test_pbo_detects_selected_strategy_that_fails_oos():
    matrix = np.array([
        [10, 1], [10, 1], [10, 1], [10, 1],
        [-10, 1], [-10, 1], [-10, 1], [-10, 1],
    ], dtype=float)
    result = probability_of_backtest_overfitting(matrix)
    assert result["pbo"] > 0.5
    assert result["splits"] == 70

def test_pbo_rejects_odd_blocks_nonfinite_and_split_explosion():
    with pytest.raises(ValueError, match="even"):
        probability_of_backtest_overfitting(np.ones((5, 2)))
    bad = np.ones((4, 2))
    bad[0, 0] = np.nan
    with pytest.raises(ValueError, match="finite"):
        probability_of_backtest_overfitting(bad)
    with pytest.raises(ValueError, match="above max_splits"):
        probability_of_backtest_overfitting(np.ones((10, 2)), max_splits=5)

def test_pbo_tie_ranks_are_finite():
    result = probability_of_backtest_overfitting(np.ones((4, 3)))
    assert result["pbo"] == 0.0
    assert np.isfinite(result["mean_logit_rank"])
