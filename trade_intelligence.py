"""ChartAnalyzer V6.6 Trade Intelligence Engine.

Descriptive diagnostics over completed trades. This module does not tune
parameters, alter signals, or infer causality from small samples.
"""
from __future__ import annotations
import math
from collections import defaultdict
from typing import Any, Dict, Iterable, List

def _num(x, default=0.0):
    try:
        v=float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError, OverflowError):
        return default

def _stats(rows):
    if not rows: return {"trades":0,"win_rate_pct":None,"expectancy_pct":None,"profit_factor":None,"avg_cost_drag_pct":None,"avg_mfe_pct":None,"avg_mae_pct":None}
    rets=[_num(t.get("net_ret")) for t in rows]
    wins=sum(x for x in rets if x>0); losses=-sum(x for x in rets if x<0)
    costs=[_num(t.get("gross_account_ret", t.get("gross_ret")))-_num(t.get("net_ret")) for t in rows]
    pf=wins/losses if losses else (math.inf if wins else None)
    cost_keys=("fee_drag_pct","spread_slippage_drag_pct","funding_drag_pct")
    has_cost_detail=any(any(k in t for k in cost_keys) for t in rows)
    cost_detail={k:round(sum(_num(t.get(k)) for t in rows)/len(rows),4) for k in cost_keys} if has_cost_detail else None
    gross_rets=[_num(t.get("gross_account_ret", t.get("gross_ret"))) for t in rows]
    net_winners=sum(x>0 for x in rets); net_losers=sum(x<0 for x in rets); net_flat=sum(x==0 for x in rets)
    gross_winners=sum(x>0 for x in gross_rets)
    return {"trades":len(rows),"win_rate_pct":round(net_winners/len(rets)*100,2),
      "gross_win_rate_pct":round(gross_winners/len(gross_rets)*100,2),
      "net_win_rate_pct":round(net_winners/len(rets)*100,2),
      "gross_winners":gross_winners,"net_winners":net_winners,"net_losers":net_losers,"net_breakeven":net_flat,
      "expectancy_pct":round(sum(rets)/len(rets)*100,4),"profit_factor":round(pf,4) if pf is not None and math.isfinite(pf) else pf,
      "avg_cost_drag_pct":round(sum(costs)/len(costs)*100,4),"avg_cost_components_pct":cost_detail,
      "avg_mfe_pct":round(sum(_num(t.get("mfe")) for t in rows)/len(rows)*100,4),
      "avg_mae_pct":round(sum(_num(t.get("mae")) for t in rows)/len(rows)*100,4)}

def _group(rows,key):
    buckets=defaultdict(list)
    for t in rows: buckets[str(t.get(key) if t.get(key) is not None else "unknown")].append(t)
    return {k:_stats(v) for k,v in sorted(buckets.items())}

def analyze_trade_intelligence(trades: Iterable[Dict[str,Any]], min_sample:int=30)->Dict[str,Any]:
    rows=[dict(t) for t in (trades or []) if isinstance(t,dict)]
    if not rows:
        return {"version":"6.7.0","status":"insufficient_data","trades":0,"message":"No completed trades."}
    for t in rows:
        t["cost_drag"]=_num(t.get("gross_account_ret", t.get("gross_ret")))-_num(t.get("net_ret"))
        t["holding_bucket"]=("1-3" if _num(t.get("bars_held"))<=3 else "4-12" if _num(t.get("bars_held"))<=12 else "13-48" if _num(t.get("bars_held"))<=48 else "49+")
        score=t.get("entry_signal_score")
        t["entry_signal_quality"]=("strong" if score is not None and abs(_num(score))>=0.5 else "moderate" if score is not None and abs(_num(score))>=0.2 else "weak_or_unknown")
    overall=_stats(rows)
    losers=[t for t in rows if _num(t.get("net_ret"))<0]
    winners=[t for t in rows if _num(t.get("net_ret"))>0]
    # MAE is a signed adverse return (zero or negative); values below -1%
    # identify larger adverse excursions among losing trades.
    entry_issue=[t for t in losers if _num(t.get("mae")) < -0.01]
    exit_capture=[t for t in losers if _num(t.get("mfe")) > 0.002]
    return {"version":"6.7.0","status":"descriptive_only","trades":len(rows),"overall":overall,
      "by_side":_group(rows,"side"),"by_entry_regime":_group(rows,"entry_regime"),
      "by_entry_signal_quality":_group(rows,"entry_signal_quality"),"by_exit_reason":_group(rows,"exit_reason"),
      "by_holding_duration":_group(rows,"holding_bucket"),
      "failure_diagnostics":{"losers_with_mae_below_minus_1pct":len(entry_issue),"losers_with_positive_mfe_above_0_2pct":len(exit_capture),
        "entry_risk_hypothesis":"Large adverse excursion among losing trades may warrant review of entry timing or risk limits; not causal proof.",
        "exit_capture_hypothesis":"Positive favorable excursion among losing trades may warrant review of exit capture; not causal proof."},
      "sample_warning":None if len(rows)>=min_sample else f"Only {len(rows)} trades; below the {min_sample}-trade diagnostic threshold.",
      "cost_drag_note":"Cost Drag compares gross account-level return before costs with net account-level return. Component averages reflect only configured charges recorded by the engine; they are not estimates of actual order-book spread or slippage. Component averages may not sum exactly because costs compound.",
      "excursion_method":"open_to_open",
      "excursion_note":"MFE/MAE use signed open-to-open returns: MFE is non-negative and MAE is non-positive. They do not resolve intrabar High/Low ordering or exact stop/target touch sequence.",
      "guardrail":"No parameter fitting, optimization, signal modification, or profitability claim."}

__all__=["analyze_trade_intelligence"]
