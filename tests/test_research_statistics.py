import numpy as np
from research_statistics import moving_block_bootstrap, block_bootstrap_mean_test, block_signal_permutation, benjamini_hochberg


def test_bootstrap_reproducible_and_ci_ordered():
    x=np.random.default_rng(3).normal(.2,.3,300)
    a=moving_block_bootstrap(x,block_length=5,n_boot=500,seed=9)
    b=moving_block_bootstrap(x,block_length=5,n_boot=500,seed=9)
    assert a==b and a['ci'][0] < a['ci'][1]


def test_mean_test_pvalue_is_valid():
    x=np.random.default_rng(4).normal(0,.1,250)
    out=block_bootstrap_mean_test(x,block_length=5,n_boot=500)
    assert 0 < out['p_value'] <= 1


def test_signal_permutation_has_explicit_scope_and_valid_p():
    rng=np.random.default_rng(5); s=np.zeros(300); s[::7]=1
    r=rng.normal(0,.01,300)
    out=block_signal_permutation(s,r,n_permutations=100,block_length=12)
    assert 0 < out['p_value_one_sided'] <= 1
    assert 'entry timing diagnostic only' in out['scope']


def test_bh_adjustment_is_monotone_in_rank():
    p=np.array([.01,.04,.03,.2])
    q=benjamini_hochberg(p)
    assert np.all((q>=0)&(q<=1))
    assert q[0] <= q[2] <= q[1]


def test_matched_random_entry_preserves_trade_run_lengths():
    from research_statistics import matched_random_entry_control
    s=np.zeros(200,dtype=int); s[10:14]=1; s[40:42]=1; s[100:107]=1
    r=np.random.default_rng(22).normal(0,.01,200)
    out=matched_random_entry_control(s,r,n_simulations=100)
    assert out['holding_run_lengths']==[4,2,7]
    assert out['trade_count']==3
    assert 0 < out['p_value_one_sided'] <= 1
