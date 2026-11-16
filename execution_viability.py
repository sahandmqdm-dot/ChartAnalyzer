"""Execution economics gate for ChartAnalyzer V6.9.1.

Research diagnostics only. Uses completed, executed backtest trades. It does not
optimize entries/exits, alter signals, or authorize live execution.
"""
from __future__ import annotations
import math
from typing import Any, Dict, Iterable, Optional
import numpy as np

ENGINE_VERSION = "ExecutionViability V1.0"


def _finite(value: Any) -> Optional[float]:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError, OverflowError):
        return None


def analyze_execution_viability(trades: Iterable[Dict[str, Any]], *, min_samples: int = 20) -> Dict[str, Any]:
    """Measure whether observed favorable excursion can plausibly clear modeled costs.

    MFE is diagnostic, not a realizable exit price. Intrabar MFE has path/order
    ambiguity and must never be interpreted as executable captured profit.
    """
    if min_samples < 2:
        raise ValueError("min_samples must be >= 2")
    rows = [t for t in (trades or []) if isinstance(t, dict)]
    valid = []
    excluded = 0
    for t in rows:
        gross = _finite(t.get("gross_account_ret"))
        net = _finite(t.get("net_ret"))
        if gross is None or net is None or net <= -1:
            excluded += 1
            continue
        drag = max(0.0, gross - net)
        mfe = _finite(t.get("intrabar_mfe"))
        if mfe is None:
            mfe = _finite(t.get("mfe"))
        valid.append({"gross": gross, "net": net, "drag": drag, "mfe": mfe})

    n = len(valid)
    if not n:
        return {"engine": ENGINE_VERSION, "status": "RESEARCH_ONLY", "verdict": "INSUFFICIENT_DATA",
                "samples": 0, "excluded": excluded, "metrics": {},
                "guardrails": ["Completed executed trades with gross_account_ret and net_ret are required."]}

    gross = np.asarray([x["gross"] for x in valid])
    net = np.asarray([x["net"] for x in valid])
    drag = np.asarray([x["drag"] for x in valid])
    mfe_rows = [x for x in valid if x["mfe"] is not None and x["mfe"] >= 0]
    mfe = np.asarray([x["mfe"] for x in mfe_rows], dtype=float)
    avg_drag = float(drag.mean())
    avg_mfe = float(mfe.mean()) if len(mfe) else None
    coverage = float(np.mean(mfe >= drag[:len(mfe)])) if len(mfe) == n else None
    # Pairwise hurdle coverage only when every row has a valid MFE.
    if len(mfe) == n:
        coverage = float(np.mean(mfe >= drag))
    else:
        coverage = None

    if n < min_samples:
        verdict = "INSUFFICIENT_SAMPLE"
    elif float(net.mean()) < 0 and avg_mfe is not None and avg_mfe < avg_drag:
        verdict = "COSTS_DOMINATE_FAVORABLE_EXCURSION"
    elif float(net.mean()) < 0:
        verdict = "NEGATIVE_NET_EXPECTANCY"
    elif avg_mfe is not None and avg_mfe < avg_drag:
        verdict = "FAVORABLE_EXCURSION_BELOW_COST_HURDLE"
    else:
        verdict = "REQUIRES_OUT_OF_SAMPLE_VALIDATION"

    return {
        "engine": ENGINE_VERSION, "status": "RESEARCH_ONLY", "verdict": verdict,
        "samples": n, "excluded": excluded,
        "metrics": {
            "mean_gross_return_pct": round(float(gross.mean()) * 100, 5),
            "mean_net_return_pct": round(float(net.mean()) * 100, 5),
            "mean_modeled_cost_drag_pct": round(avg_drag * 100, 5),
            "mean_intrabar_mfe_pct": round(avg_mfe * 100, 5) if avg_mfe is not None else None,
            "mfe_to_cost_ratio": round(avg_mfe / avg_drag, 4) if avg_mfe is not None and avg_drag > 0 else None,
            "trades_mfe_exceeded_cost_pct": round(coverage * 100, 3) if coverage is not None else None,
            "gross_expectancy_needed_to_offset_mean_cost_pct": round(avg_drag * 100, 5),
            "net_positive_trade_pct": round(float(np.mean(net > 0)) * 100, 3),
        },
        "interpretation": [
            "Modeled cost drag is derived from gross_account_ret minus net_ret.",
            "MFE is an excursion diagnostic, not proof that a realizable exit was available.",
            "Intrabar high/low ordering and stop/target sequencing may be unknown.",
            "A positive MFE-to-cost ratio does not establish a profitable exit rule.",
        ],
        "guardrails": [
            "Research only: does not change signals, tune parameters, or authorize live trading.",
            "Validate any proposed execution or exit change on chronological unseen data.",
            "Use actual venue fees, spread, slippage, funding, and realistic fill assumptions.",
        ],
    }


__all__ = ["analyze_execution_viability"]
