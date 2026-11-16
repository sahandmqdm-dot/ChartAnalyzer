"""V6.6.5 evidence-first diagnostics for backtest research.

Produces descriptive findings only. It does not optimize parameters, alter
signals, or claim that a pattern will persist out of sample.
"""
from __future__ import annotations
import math
from typing import Any, Dict, Iterable


def _f(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError, OverflowError):
        return None


def build_research_diagnostics(
    trades: Iterable[Dict[str, Any]],
    backtest: Dict[str, Any] | None = None,
    *, market_type: str = "spot", position_size: float = 1.0, leverage: float = 1.0,
    min_sample: int = 30,
) -> Dict[str, Any]:
    rows = [t for t in (trades or []) if isinstance(t, dict)]
    bt = backtest or {}
    n = len(rows)
    net = [_f(t.get("net_ret")) for t in rows]
    net = [x for x in net if x is not None]
    gross = [_f(t.get("gross_account_ret", t.get("gross_ret"))) for t in rows]
    gross = [x for x in gross if x is not None]
    costs = [(ga - nr) for t in rows if (ga := _f(t.get("gross_account_ret", t.get("gross_ret")))) is not None and (nr := _f(t.get("net_ret"))) is not None]
    mfe = [_f(t.get("mfe")) for t in rows]
    mfe = [x for x in mfe if x is not None]
    mae = [_f(t.get("mae")) for t in rows]
    mae = [x for x in mae if x is not None]
    winners = sum(x > 0 for x in net)
    losers = sum(x < 0 for x in net)
    reasons = {}
    regimes = {}
    durations = {}
    for t in rows:
        reason = str(t.get("exit_reason", "unknown"))
        reasons[reason] = reasons.get(reason, 0) + 1
        regime = str(t.get("entry_regime", "unknown"))
        regimes[regime] = regimes.get(regime, 0) + 1
        try:
            bars = int(t.get("bars_held", 0) or 0)
        except (TypeError, ValueError):
            bars = 0
        bucket = "1-3" if bars <= 3 else "4-12" if bars <= 12 else "13-48" if bars <= 48 else "49+"
        durations[bucket] = durations.get(bucket, 0) + 1

    mean_net = sum(net) / len(net) * 100 if net else None
    mean_cost = sum(costs) / len(costs) * 100 if costs else None
    mean_mfe = sum(mfe) / len(mfe) * 100 if mfe else None
    mean_mae = sum(mae) / len(mae) * 100 if mae else None
    winners_positive_mfe = sum(
        1 for t in rows
        if (_f(t.get("net_ret")) or 0) > 0 and (_f(t.get("mfe")) or 0) > 0
    )
    losers_positive_mfe = sum(
        1 for t in rows
        if (_f(t.get("net_ret")) or 0) < 0 and (_f(t.get("mfe")) or 0) > 0.002
    )
    pf = _f(bt.get("net_profit_factor", bt.get("profit_factor")))
    if pf is None and net:
        gains = sum(x for x in net if x > 0)
        losses = -sum(x for x in net if x < 0)
        pf = gains / losses if losses else (math.inf if gains else 0.0)
    strategy = _f(bt.get("strategy_pct"))
    benchmark = _f(bt.get("buyhold_net_pct", bt.get("buyhold_pct")))
    sample_ok = n >= min_sample

    findings = []
    if not sample_ok:
        findings.append({"level": "warning", "code": "SMALL_SAMPLE",
            "finding": f"Only {n} completed trades; below the {min_sample}-trade diagnostic threshold.",
            "interpretation": "All subgroup patterns are exploratory and unstable."})
    if mean_net is not None and mean_net < 0:
        findings.append({"level": "critical", "code": "NEGATIVE_EXPECTANCY",
            "finding": f"Mean net return per trade is {mean_net:.4f}%.",
            "interpretation": "The current frozen strategy loses on average in this sample."})
    if pf is not None and math.isfinite(pf) and pf < 1:
        findings.append({"level": "critical", "code": "PROFIT_FACTOR_BELOW_ONE",
            "finding": f"Net profit factor is {pf:.4f}.",
            "interpretation": "Gross/net winning contribution is insufficient to offset losing contribution."})
    if strategy is not None and benchmark is not None and strategy < benchmark:
        findings.append({"level": "warning", "code": "UNDERPERFORMS_PASSIVE",
            "finding": f"Strategy return {strategy:.3f}% trails cost-adjusted passive long {benchmark:.3f}%.",
            "interpretation": "The strategy did not outperform the passive benchmark over this window."})
    if mean_mfe is not None and mean_cost is not None:
        comparable = str(market_type).lower() == "spot" and abs(float(position_size) - 1.0) < 1e-9 and abs(float(leverage) - 1.0) < 1e-9
        if comparable and mean_mfe < mean_cost:
            findings.append({"level": "critical", "code": "FAVORABLE_EXCURSION_BELOW_COST_HURDLE",
                "finding": f"Mean open-to-open MFE ({mean_mfe:.3f}%) is below mean recorded account cost drag ({mean_cost:.3f}%).",
                "interpretation": "In this 1x spot / 100%-notional setup, typical favorable excursion is smaller than recorded costs. This is a rough descriptive comparison, not an executable exit bound; it argues for examining signal horizon and cost assumptions before adding entry frequency."})
        elif not comparable:
            findings.append({"level": "info", "code": "MFE_COST_BASIS_NOT_COMPARABLE",
                "finding": "MFE is an asset-price excursion while recorded cost drag is account-level.",
                "interpretation": "They are not directly compared because position sizing or leverage differs from 1x spot."})
    if n and reasons and set(reasons) == {"signal"}:
        findings.append({"level": "info", "code": "SIGNAL_ONLY_EXITS",
            "finding": "All completed trades in this sample closed with exit_reason=signal.",
            "interpretation": "The tested engine is exiting on signal state changes here; no stop-loss, take-profit, or time-exit reason appears in this sample."})
    if losers_positive_mfe:
        findings.append({"level": "warning", "code": "LOSERS_AFTER_FAVORABLE_EXCURSION",
            "finding": f"{losers_positive_mfe} losing trades had open-to-open MFE above 0.20%.",
            "interpretation": "Worth inspecting trade paths and exit timing, but MFE alone cannot establish that a realizable exit was available."})
    if regimes and all(
        sum((_f(t.get("net_ret")) or 0) for t in rows if str(t.get("entry_regime", "unknown")) == k) <= 0
        for k in regimes
    ):
        findings.append({"level": "warning", "code": "NO_POSITIVE_REGIME_GROUP",
            "finding": "No observed entry-regime group has positive aggregate net returns.",
            "interpretation": "The sample does not support fixing the problem simply by excluding one named regime."})

    return {
        "version": "6.7.0", "status": "descriptive_only", "trades": n,
        "sample_sufficient_for_diagnostics": sample_ok,
        "summary": {"net_winners": winners, "net_losers": losers,
            "mean_net_expectancy_pct": round(mean_net, 4) if mean_net is not None else None,
            "net_profit_factor": round(pf, 4) if pf is not None and math.isfinite(pf) else None,
            "mean_cost_drag_account_pct": round(mean_cost, 4) if mean_cost is not None else None,
            "mean_mfe_open_to_open_pct": round(mean_mfe, 4) if mean_mfe is not None else None,
            "mean_mae_open_to_open_pct": round(mean_mae, 4) if mean_mae is not None else None,
            "losers_with_mfe_above_0_20pct": losers_positive_mfe,
            "winners_with_positive_mfe": winners_positive_mfe},
        "exit_reason_counts": reasons, "entry_regime_counts": regimes,
        "holding_duration_counts": durations, "findings": findings,
        "guardrail": "Descriptive research only. No parameter fitting, rule changes, signal modification, or profitability claims. Confirm any hypothesis on separate out-of-sample data before changing the strategy."
    }


__all__ = ["build_research_diagnostics"]
