"""Dependence-aware inference utilities. Research diagnostics, not deployment authorization."""
from __future__ import annotations
import math
import numpy as np


def _clean(x):
    a=np.asarray(x,dtype=float).reshape(-1)
    a=a[np.isfinite(a)]
    if len(a)<2: raise ValueError("at least two finite observations are required")
    return a


def moving_block_bootstrap(values, *, block_length=5, n_boot=5000, confidence=0.95, seed=17):
    x=_clean(values); n=len(x); b=max(1,min(int(block_length),n)); rng=np.random.default_rng(seed)
    starts=np.arange(n)
    means=np.empty(int(n_boot),dtype=float)
    need=math.ceil(n/b)
    for k in range(int(n_boot)):
        s=rng.choice(starts,size=need,replace=True)
        idx=np.concatenate([(v+np.arange(b))%n for v in s])[:n]
        means[k]=float(np.mean(x[idx]))
    alpha=(1-float(confidence))/2
    lo,hi=np.quantile(means,[alpha,1-alpha])
    return {"mean":float(np.mean(x)),"ci": [float(lo),float(hi)],"confidence":float(confidence),
            "block_length":b,"n":n,"bootstrap_samples":int(n_boot),"method":"circular moving-block bootstrap"}


def block_bootstrap_mean_test(values, *, block_length=5, n_boot=5000, seed=23):
    """One-sided H0: mean <= 0, using centered circular block bootstrap null."""
    x=_clean(values); observed=float(x.mean()); centered=x-observed
    n=len(x); b=max(1,min(int(block_length),n)); rng=np.random.default_rng(seed)
    need=math.ceil(n/b); null=np.empty(int(n_boot))
    for k in range(int(n_boot)):
        s=rng.integers(0,n,size=need)
        idx=np.concatenate([(v+np.arange(b))%n for v in s])[:n]
        null[k]=float(centered[idx].mean())
    p=float((1+np.sum(null>=observed))/(len(null)+1))
    return {"statistic":observed,"p_value":p,"n":n,"block_length":b,
            "bootstrap_samples":int(n_boot),"method":"centered circular moving-block bootstrap, one-sided"}


def block_signal_permutation(signal, forward_returns, *, cost_per_turnover=0.0,
                              block_length=24, n_permutations=1000, seed=41):
    """Block-order permutation control for a causal long/flat signal.

    Signal blocks are reordered while market returns stay chronological. Costs are
    charged on position changes. This tests timing association, not stop/target execution.
    """
    s=np.asarray(signal,dtype=float).reshape(-1); r=np.asarray(forward_returns,dtype=float).reshape(-1)
    if len(s)!=len(r) or len(s)<64: raise ValueError("signal/return arrays must match and contain >=64 bars")
    if not np.isfinite(s).all() or not np.isfinite(r).all(): raise ValueError("non-finite input")
    if not np.isin(s,[0,1]).all(): raise ValueError("signal must be binary long/flat")
    b=max(2,min(int(block_length),len(s))); rng=np.random.default_rng(seed)
    blocks=[s[i:i+b] for i in range(0,len(s),b)]
    def score(pos):
        prev=np.r_[0.0,pos[:-1]]
        turnover=np.abs(pos-prev)
        return float(np.mean(pos*r-turnover*float(cost_per_turnover)))
    observed=score(s); null=np.empty(int(n_permutations))
    for k in range(int(n_permutations)):
        order=rng.permutation(len(blocks))
        shuffled=np.concatenate([blocks[i] for i in order])[:len(s)]
        null[k]=score(shuffled)
    p=float((1+np.sum(null>=observed))/(len(null)+1))
    return {"observed_mean_net_bar_return":observed,"null_mean":float(null.mean()),
            "null_95pct": [float(v) for v in np.quantile(null,[.025,.975])],
            "p_value_one_sided":p,"n_bars":len(s),"block_length":b,
            "permutations":int(n_permutations),"method":"block-order signal permutation",
            "scope":"entry timing diagnostic only; excludes ATR stops/targets and is not full-strategy proof"}


def benjamini_hochberg(p_values):
    p=np.asarray(p_values,dtype=float)
    if p.ndim!=1 or not np.isfinite(p).all() or ((p<0)|(p>1)).any(): raise ValueError("p-values must be finite in [0,1]")
    m=len(p)
    if not m:return np.array([],dtype=float)
    order=np.argsort(p); ranked=p[order]; q=np.empty(m); running=1.0
    for i in range(m-1,-1,-1):
        running=min(running,ranked[i]*m/(i+1)); q[order[i]]=min(1.0,running)
    return q


def matched_random_entry_control(signal, forward_returns, *, cost_per_turnover=0.0,
                                  n_simulations=1000, seed=73):
    """Random-entry baseline preserving the observed long-trade holding lengths.

    It randomizes the placement of each contiguous long run over the same sample,
    preserving run lengths and total invested bars. It is a benchmark, not a full
    exchange execution simulation.
    """
    s=np.asarray(signal,dtype=int).reshape(-1); r=np.asarray(forward_returns,dtype=float).reshape(-1)
    if len(s)!=len(r) or len(s)<64: raise ValueError("signal/return arrays must match and contain >=64 bars")
    if not np.isfinite(r).all() or not np.isin(s,[0,1]).all(): raise ValueError("invalid signal or return values")
    starts=np.flatnonzero((s==1)&np.r_[True,s[:-1]==0])
    ends=np.flatnonzero((s==1)&np.r_[s[1:]==0,True])+1
    lengths=(ends-starts).astype(int)
    if not len(lengths): raise ValueError("observed signal contains no long trades")
    if int(lengths.sum())>len(s): raise ValueError("invalid run-length structure")
    def score(pos):
        turnover=np.abs(pos-np.r_[0,pos[:-1]])
        return float(np.mean(pos*r-turnover*float(cost_per_turnover)))
    observed=score(s.astype(float)); rng=np.random.default_rng(seed); null=np.empty(int(n_simulations))
    occupied=len(s)-int(lengths.sum())
    for k in range(int(n_simulations)):
        # Randomly allocate free bars into m+1 gaps, then place original run lengths.
        gaps=rng.multinomial(occupied,np.ones(len(lengths)+1)/(len(lengths)+1))
        pos=np.zeros(len(s),dtype=float); cursor=int(gaps[0])
        for j,L in enumerate(lengths):
            pos[cursor:cursor+L]=1.0
            cursor+=int(L)+int(gaps[j+1])
        null[k]=score(pos)
    p=float((1+np.sum(null>=observed))/(len(null)+1))
    return {"observed_mean_net_bar_return":observed,"random_mean":float(null.mean()),
            "random_95pct":[float(v) for v in np.quantile(null,[.025,.975])],
            "p_value_one_sided":p,"n_bars":len(s),"trade_count":int(len(lengths)),
            "holding_run_lengths":lengths.tolist(),"simulations":int(n_simulations),
            "method":"matched random entry with preserved holding-run lengths",
            "scope":"entry timing benchmark only; does not model ATR stops/targets or order-book fills"}
