"""Chronological split and purged/embargoed validation primitives."""
from __future__ import annotations
import numpy as np


def chronological_split(n, *, train_ratio=.60, validation_ratio=.20, holdout_ratio=.20, min_each=1):
    if int(n) != n or n < 3: raise ValueError("n must be an integer >= 3")
    ratios=np.asarray([train_ratio,validation_ratio,holdout_ratio],dtype=float)
    if not np.isfinite(ratios).all() or (ratios<=0).any() or not np.isclose(ratios.sum(),1.0,atol=1e-8):
        raise ValueError("split ratios must be positive and sum to 1")
    n_train=int(n*ratios[0]); n_val=int(n*ratios[1]); n_hold=n-n_train-n_val
    if min(n_train,n_val,n_hold)<int(min_each): raise ValueError("not enough observations for requested split")
    return {"train":(0,n_train),"validation":(n_train,n_train+n_val),
            "holdout":(n_train+n_val,n),"chronological":True,
            "holdout_policy":"final segment only; do not use for tuning"}


def purged_walk_forward_splits(n, *, n_splits=5, min_train=100, test_size=None,
                               label_end_indices=None, embargo_bars=0):
    n=int(n); n_splits=int(n_splits); min_train=int(min_train); embargo=int(embargo_bars)
    if n<=0 or n_splits<2 or min_train<1 or embargo<0: raise ValueError("invalid split parameters")
    if test_size is None: test_size=(n-min_train-embargo*(n_splits-1))//n_splits
    test_size=int(test_size)
    if test_size<1: raise ValueError("test_size too small")
    ends=None if label_end_indices is None else np.asarray(label_end_indices,dtype=int)
    if ends is not None and len(ends)!=n: raise ValueError("label_end_indices length mismatch")
    folds=[]
    for k in range(n_splits):
        start=min_train+k*(test_size+embargo); end=start+test_size
        if end>n: break
        train=np.arange(0,start,dtype=int)
        purged=np.array([],dtype=int)
        if ends is not None:
            purged=train[ends[train]>=start]
            train=train[ends[train]<start]
        if len(train)<min_train: continue
        folds.append({"fold":k+1,"train_indices":train,"test_indices":np.arange(start,end,dtype=int),
                      "purged_indices":purged,"embargo_after_test":embargo,
                      "train_end_exclusive":start,"test_start":start,"test_end_exclusive":end})
    if len(folds)<2: raise ValueError("fewer than two valid purged folds; reduce purge/embargo or provide more data")
    return folds


def audit_feature_availability(feature_available_at, decision_times):
    a=np.asarray(feature_available_at,dtype=float); d=np.asarray(decision_times,dtype=float)
    if a.shape!=d.shape: raise ValueError("availability and decision arrays must have same shape")
    bad=np.flatnonzero(~np.isfinite(a)|~np.isfinite(d)|(a>d))
    return {"status":"PASS" if len(bad)==0 else "BLOCKED","violations":bad.astype(int).tolist(),
            "policy":"Every feature must have been available no later than its decision timestamp."}
