import numpy as np
import pytest

from research_statistics import (
    moving_block_bootstrap, block_bootstrap_mean_test,
    block_signal_permutation, benjamini_hochberg,
)


def test_bootstrap_reproducible_and_ci_ordered():
    x = np.random.default_rng(3).normal(.2, .3, 300)
    a = moving_block_bootstrap(x, block_length=5, n_boot=500, seed=9)
    b = moving_block_bootstrap(x, block_length=5, n_boot=500, seed=9)
    assert a == b and a["ci"][0] < a["ci"][1]


def test_mean_test_pvalue_is_valid():
    x = np.random.default_rng(4).normal(0, .1, 250)
    out = block_bootstrap_mean_test(x, block_length=5, n_boot=500)
    assert 0 < out["p_value"] <= 1


def test_signal_permutation_has_explicit_scope_and_valid_p():
    rng = np.random.default_rng(5)
    s = np.zeros(300)
    s[::7] = 1
    r = rng.normal(0, .01, 300)
    out = block_signal_permutation(s, r, n_permutations=100, block_length=12)
    assert 0 < out["p_value_one_sided"] <= 1
    assert "entry timing diagnostic only" in out["scope"]


def test_bh_adjustment_is_monotone_in_rank():
    p = np.array([.01, .04, .03, .2])
    q = benjamini_hochberg(p)
    assert np.all((q >= 0) & (q <= 1))
    assert q[0] <= q[2] <= q[1]


def test_matched_random_entry_preserves_trade_run_lengths():
    from research_statistics import matched_random_entry_control
    s = np.zeros(200, dtype=int)
    s[10:14] = 1
    s[40:42] = 1
    s[100:107] = 1
    r = np.random.default_rng(22).normal(0, .01, 200)
    out = matched_random_entry_control(s, r, n_simulations=100)
    assert out["holding_run_lengths"] == [4, 2, 7]
    assert out["trade_count"] == 3
    assert 0 < out["p_value_one_sided"] <= 1


@pytest.mark.parametrize("values", [
    [0.1, float("nan"), 0.2],
    [0.1, float("inf"), 0.2],
    [[0.1, 0.2], [0.3, 0.4]],
])
def test_bootstrap_rejects_nonfinite_or_non_1d_series(values):
    with pytest.raises(ValueError):
        moving_block_bootstrap(values, n_boot=20)


@pytest.mark.parametrize("kwargs", [
    {"n_boot": 0},
    {"n_boot": -1},
    {"confidence": 0},
    {"confidence": 1},
    {"confidence": float("nan")},
    {"block_length": 1.5},
])
def test_bootstrap_rejects_invalid_inference_parameters(kwargs):
    x = np.arange(20, dtype=float)
    with pytest.raises(ValueError):
        moving_block_bootstrap(x, **kwargs)


@pytest.mark.parametrize("signal", [
    np.r_[np.full(32, 0.5), np.full(32, 1.5)],
    np.r_[np.zeros(63), np.nan],
])
def test_random_entry_rejects_nonbinary_or_nonfinite_signal(signal):
    from research_statistics import matched_random_entry_control
    with pytest.raises(ValueError):
        matched_random_entry_control(signal, np.zeros(64), n_simulations=10)


@pytest.mark.parametrize("kwargs", [
    {"cost_per_turnover": -0.01},
    {"cost_per_turnover": float("nan")},
    {"n_simulations": 0},
])
def test_random_entry_rejects_invalid_cost_or_simulation_count(kwargs):
    from research_statistics import matched_random_entry_control
    signal = np.zeros(64)
    signal[10:12] = 1
    with pytest.raises(ValueError):
        matched_random_entry_control(signal, np.zeros(64), **kwargs)


@pytest.mark.parametrize("kwargs", [
    {"cost_per_turnover": -0.01},
    {"n_permutations": 0},
    {"block_length": 1.5},
])
def test_signal_permutation_rejects_invalid_parameters(kwargs):
    signal = np.zeros(64)
    signal[::4] = 1
    with pytest.raises(ValueError):
        block_signal_permutation(signal, np.zeros(64), **kwargs)
