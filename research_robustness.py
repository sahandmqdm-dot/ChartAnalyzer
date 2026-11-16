"""Robustness summaries across parameter neighborhoods, regimes and symbols."""
from __future__ import annotations
import math


def parameter_neighborhood_stability(rows, *, expectancy_key="net_expectancy_pct", min_positive_ratio=.70,
                                     near_best_tolerance_pct=25.0):
    valid=[r for r in rows if isinstance(r,dict) and _num(r.get(expectancy_key)) is not None]
    if not valid: return {"status":"INSUFFICIENT_EVIDENCE","n":0}
    values=[_num(r[expectancy_key]) for r in valid]
    positive=sum(v>0 for v in values); best=max(values)
    tolerance=abs(best)*float(near_best_tolerance_pct)/100
    near=sum(v>=best-tolerance for v in values)
    ratio=positive/len(values)
    return {"status":"PASS" if ratio>=min_positive_ratio else "FAIL",
            "n_parameter_variants":len(values),"positive_variants":positive,
            "positive_ratio":ratio,"near_best_variants":near,
            "best_expectancy_pct":best,"median_expectancy_pct":sorted(values)[len(values)//2],
            "threshold":min_positive_ratio,
            "warning":"Variants must be predeclared; this summary does not correct selection bias."}


def cross_symbol_stability(rows, *, min_symbols=3, expectancy_key="net_expectancy_pct"):
    by={}
    for r in rows:
        if not isinstance(r,dict): continue
        sym=str(r.get("symbol") or "").upper(); val=_num(r.get(expectancy_key))
        if sym and val is not None: by.setdefault(sym,[]).append(val)
    means={k:sum(v)/len(v) for k,v in by.items()}
    positives=sum(v>0 for v in means.values())
    enough=len(means)>=int(min_symbols)
    return {"status":"PASS" if enough and positives/len(means)>=.70 else ("INSUFFICIENT_EVIDENCE" if not enough else "FAIL"),
            "symbols":means,"symbol_count":len(means),"positive_symbols":positives,
            "positive_symbol_ratio":positives/len(means) if means else None,"minimum_symbols":int(min_symbols)}


def regime_stability(rows, *, expectancy_key="net_expectancy_pct", min_trades_per_regime=30):
    out={}
    for r in rows:
        if not isinstance(r,dict): continue
        regime=str(r.get("regime") or "unknown"); n=int(r.get("trades",0) or 0); exp=_num(r.get(expectancy_key))
        if exp is None: continue
        out.setdefault(regime,[]).append((n,exp))
    regimes={}
    for name, vals in out.items():
        n=sum(x[0] for x in vals); weighted=sum(x[0]*x[1] for x in vals)/n if n else None
        regimes[name]={"trades":n,"net_expectancy_pct":weighted,
                       "evidence":"ADEQUATE" if n>=min_trades_per_regime else "INSUFFICIENT"}
    adequate=[v for v in regimes.values() if v["evidence"]=="ADEQUATE"]
    return {"status":"INSUFFICIENT_EVIDENCE" if len(adequate)<2 else
            ("PASS" if all(v["net_expectancy_pct"]>0 for v in adequate) else "FAIL"),
            "regimes":regimes,"adequate_regime_count":len(adequate)}


def _num(x):
    try:
        v=float(x)
        return v if math.isfinite(v) else None
    except (TypeError,ValueError): return None
