"""Safe declarative long/flat rule compiler for pre-registered research hypotheses.

No Python/eval expressions are accepted. Every feature is trailing/causal and
signals are emitted at candle close for next-open execution.
"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd

OPS={">",">=","<","<=","==","!=","between","crosses_above","crosses_below"}


def build_feature_frame(df, indicators):
    f=pd.DataFrame(index=df.index)
    for c in ("open","high","low","close","volume"):
        if c in df: f[c]=pd.to_numeric(df[c],errors="coerce")
    f["score"]=indicators["S"].astype(float)
    x=indicators.get("x")
    if x is not None:
        for name in ("atr","adx"):
            if hasattr(x,name): f[name]=getattr(x,name).astype(float)
    for name,series in (indicators.get("gs") or {}).items(): f[f"group_{name}"]=series.astype(float)
    for name,series in (indicators.get("sig") or {}).items(): f[f"indicator_{name}"]=series.astype(float)
    for name,series in (indicators.get("val") or {}).items(): f[f"value_{name}"]=series.astype(float)
    if indicators.get("regime") is not None: f["regime"]=indicators["regime"].astype(str)
    mri=indicators.get("market_regime_intelligence")
    if mri is not None:
        if "regime" in mri: f["market_regime"]=mri["regime"].astype(str)
        if "transition" in mri: f["regime_transition"]=mri["transition"].astype(bool)
    return f.replace([np.inf,-np.inf],np.nan)


def _condition(features, spec):
    if not isinstance(spec, dict):
        raise ValueError("each condition must be an object")
    if set(spec) - {"feature", "op", "value", "value2"}:
        raise ValueError("unsupported condition fields")
    feature = str(spec.get("feature") or "")
    op = str(spec.get("op") or "")
    if not feature or len(feature) > 64:
        raise ValueError("feature name must contain 1..64 characters")
    if op not in OPS:
        raise ValueError("unsupported operator: " + op)
    if "value" not in spec:
        raise ValueError("condition value is required")
    for key in ("value", "value2"):
        value = spec.get(key)
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise ValueError("condition values must be scalar")
        if isinstance(value, str) and len(value) > 256:
            raise ValueError("condition string values cannot exceed 256 characters")
        if key in spec and isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(float(value)):
                raise ValueError("condition numeric values must be finite")
        if op in {">", ">=", "<", "<=", "between", "crosses_above", "crosses_below"} and isinstance(value, bool):
            raise ValueError("numeric operators require numeric, non-boolean values")
    if feature not in features.columns: raise ValueError(f"unknown research feature: {feature}")
    x=features[feature]
    if op=="crosses_above":
        y=float(spec["value"]); return (x.shift(1)<=y)&(x>y)
    if op=="crosses_below":
        y=float(spec["value"]); return (x.shift(1)>=y)&(x<y)
    if op=="between":
        lo=float(spec["value"]); hi=float(spec["value2"])
        if hi<lo: raise ValueError("between value2 must be >= value")
        return (pd.to_numeric(x,errors="coerce")>=lo)&(pd.to_numeric(x,errors="coerce")<=hi)
    y=spec.get("value")
    if op in {">",">=","<","<="}:
        a=pd.to_numeric(x,errors="coerce"); b=float(y)
        return {">":a>b,">=":a>=b,"<":a<b,"<=":a<=b}[op]
    if op=="==": return x.astype(str)==str(y)
    if op=="!=": return x.astype(str)!=str(y)
    raise ValueError("unsupported condition")


def _group(features, group):
    if not isinstance(group, dict) or set(group) - {"all", "any"}:
        raise ValueError("entry/exit must contain only an all or any condition group")
    all_rules=group.get("all"); any_rules=group.get("any")
    if (all_rules is None)==(any_rules is None): raise ValueError("condition group must contain exactly one of 'all' or 'any'")
    rules=all_rules if all_rules is not None else any_rules
    if not isinstance(rules,list) or not rules: raise ValueError("condition list cannot be empty")
    if len(rules) > 32:
        raise ValueError("a condition group cannot contain more than 32 rules")
    masks=[_condition(features,r).fillna(False).to_numpy(dtype=bool) for r in rules]
    return np.logical_and.reduce(masks) if all_rules is not None else np.logical_or.reduce(masks)


def compile_long_flat_rule(features, specification):
    """Compile a pre-registered entry/exit specification into a causal 0/1 signal."""
    if not isinstance(specification,dict): raise ValueError("rule specification must be an object")
    if set(specification)-{"entry","exit","max_holding_bars","description","version"}:
        raise ValueError("unsupported top-level rule fields")
    for key in ("description", "version"):
        value = specification.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > 256):
            raise ValueError(key + " must be a string of at most 256 characters")
    if "entry" not in specification: raise ValueError("entry rule is required")
    entry_group = specification["entry"]
    exit_group = specification.get("exit")
    total_rules = sum(len(g.get("all", g.get("any", []))) for g in (entry_group, exit_group)
                      if isinstance(g, dict))
    if total_rules > 64:
        raise ValueError("a rule specification cannot contain more than 64 conditions")
    entry=_group(features,entry_group)
    exit_mask=_group(features,exit_group) if exit_group is not None else np.zeros(len(features),dtype=bool)
    max_hold=specification.get("max_holding_bars")
    if max_hold is not None and (isinstance(max_hold,bool) or not isinstance(max_hold,(int,float))
                                 or not math.isfinite(float(max_hold)) or int(max_hold)!=max_hold
                                 or not 1 <= int(max_hold) <= 5000):
        raise ValueError("max_holding_bars must be an integer from 1 to 5000")
    signal=np.zeros(len(features),dtype=float); active=False; entry_i=None
    for i in range(len(features)):
        if active and ((exit_mask[i]) or (max_hold is not None and i-entry_i>=int(max_hold))):
            active=False; entry_i=None
        elif not active and entry[i]:
            active=True; entry_i=i
        signal[i]=1.0 if active else 0.0
    return signal


__all__=["build_feature_frame","compile_long_flat_rule"]
