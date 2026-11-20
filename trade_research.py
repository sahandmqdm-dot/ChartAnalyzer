"""V6.6.5 descriptive trade and rejected-signal research.

No parameter fitting, signal changes, or causal claims. Rejected-candidate
returns are retrospective labels, not executable strategy performance.
"""
from __future__ import annotations
import math
from collections import defaultdict
from typing import Any, Dict, Iterable


def _f(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _summary(rows):
    vals = [r["net_return_pct"] for r in rows if r.get("net_return_pct") is not None]
    if not vals:
        return {"samples": len(rows), "mean_return_pct": None, "median_return_pct": None,
                "positive_pct": None, "note": "No valid observations."}
    vals_sorted = sorted(vals)
    mid = len(vals_sorted) // 2
    median = vals_sorted[mid] if len(vals_sorted) % 2 else (vals_sorted[mid-1] + vals_sorted[mid]) / 2
    return {"samples": len(vals), "mean_return_pct": round(sum(vals)/len(vals), 4),
            "median_return_pct": round(median, 4),
            "positive_pct": round(sum(v > 0 for v in vals)/len(vals)*100, 2)}


def _median(values):
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def analyze_trade_quality(trades: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = [t for t in (trades or []) if isinstance(t, dict)]
    score_bins = defaultdict(list)
    exit_rows = []
    captures = []
    mfe_values, mae_values, cost_drags = [], [], []
    winners_with_mfe = losers_with_mfe = 0
    for t in rows:
        score = _f(t.get("entry_signal_score"))
        net = _f(t.get("net_ret"))
        gross = _f(t.get("gross_ret"))
        gross_account = _f(t.get("gross_account_ret", t.get("gross_ret")))
        mfe = _f(t.get("mfe"))
        mae = _f(t.get("mae"))
        if score is None: band = "unknown"
        elif score >= .5: band = "strong_positive"
        elif score >= .2: band = "moderate_positive"
        elif score > 0: band = "weak_positive"
        elif score <= -.5: band = "strong_negative"
        elif score <= -.2: band = "moderate_negative"
        else: band = "neutral_or_weak"
        score_bins[band].append({"net_return_pct": net * 100 if net is not None else None})
        capture = (gross / mfe * 100) if gross is not None and gross > 0 and mfe is not None and mfe > 0 else None
        unused = max(0.0, mfe - max(gross or 0.0, 0.0)) * 100 if mfe is not None else None
        if mfe is not None:
            mfe_values.append(mfe * 100)
            if gross is not None and gross > 0 and mfe > 0:
                winners_with_mfe += 1
                captures.append(capture)
            elif gross is not None and gross <= 0 and mfe > 0:
                losers_with_mfe += 1
        if mae is not None:
            mae_values.append(mae * 100)
        if gross_account is not None and net is not None:
            cost_drags.append((gross_account - net) * 100)
        if gross is not None and gross <= 0 and mfe is not None and mfe > 0:
            capture_status = "loss_after_positive_favorable_excursion"
        elif gross is not None and gross > 0 and capture is not None:
            capture_status = "positive_realized_capture"
        elif mfe is not None and mfe > 0:
            capture_status = "positive_excursion_no_positive_realized_return"
        else:
            capture_status = "no_positive_open_to_open_excursion"
        exit_rows.append({"bars_held": t.get("bars_held"), "gross_return_pct": round(gross*100,4) if gross is not None else None,
                          "net_return_pct": round(net*100,4) if net is not None else None,
                          "mfe_pct": round(mfe*100,4) if mfe is not None else None,
                          "gross_mfe_capture_pct": round(capture,2) if capture is not None else None,
                          "capture_status": capture_status,
                          "unused_favorable_excursion_pct": round(unused,4) if unused is not None else None,
                          "exit_reason": str(t.get("exit_reason", "unknown"))})
    return {
        "status": "descriptive_only", "trades": len(rows),
        "entry_score_cohorts": {k: _summary(v) for k, v in sorted(score_bins.items())},
        "exit_capture": {
            "trades_with_mfe": sum(x["mfe_pct"] is not None for x in exit_rows),
            "trades_with_positive_mfe": winners_with_mfe + losers_with_mfe,
            "winning_trades_with_positive_mfe": winners_with_mfe,
            "losing_trades_with_positive_mfe": losers_with_mfe,
            "mean_gross_mfe_capture_pct": round(sum(captures) / len(captures), 2) if captures else None,
            # The mean is dominated by tiny open-to-open MFE denominators (e.g. 1711%).
            # The median is the headline statistic; outliers are counted explicitly.
            "median_gross_mfe_capture_pct": (round(_median(captures), 2) if captures else None),
            "gross_mfe_capture_n": len(captures),
            "gross_mfe_capture_over_100pct_n": sum(1 for c in captures if c > 100.0),
            "mean_unused_favorable_excursion_pct": round(sum(x["unused_favorable_excursion_pct"] for x in exit_rows if x["unused_favorable_excursion_pct"] is not None) / max(1, sum(x["unused_favorable_excursion_pct"] is not None for x in exit_rows)), 4) if any(x["unused_favorable_excursion_pct"] is not None for x in exit_rows) else None,
            "mean_mfe_pct": round(sum(mfe_values) / len(mfe_values), 4) if mfe_values else None,
            "mean_mae_pct": round(sum(mae_values) / len(mae_values), 4) if mae_values else None,
            "mean_cost_drag_account_pct": round(sum(cost_drags) / len(cost_drags), 4) if cost_drags else None,
            "records": exit_rows,
            "method_note": "MFE/MAE are open-to-open excursions. Gross MFE capture is calculated only for trades with positive realized gross return and positive MFE; negative-return trades are reported separately, because dividing a loss by MFE produces a misleading capture statistic. This is descriptive, not a simulated alternative exit."},
        "guardrail": "Small cohorts are unstable. No causal inference, exit optimization, parameter fitting, or profitability claim."
    }


def analyze_rejected_bullish_candidates(df, signal, threshold: float, horizons=(1, 3, 6, 12)) -> Dict[str, Any]:
    """Retrospectively label positive scores below the long-entry threshold.

    Entry reference is next candle open; outcome is horizon close. Observations
    overlap and exclude fees/spread/slippage, so this is not a strategy backtest.
    """
    try:
        import numpy as np
        scores = np.asarray(signal, dtype=float)
        opens = np.asarray(df["open"], dtype=float)
        closes = np.asarray(df["close"], dtype=float)
    except Exception:
        return {"status": "unavailable", "reason": "Required open/close/score arrays unavailable.", "horizons": {}}
    n = min(len(scores), len(opens), len(closes))
    if n < 5 or threshold <= 0:
        return {"status": "insufficient_data", "candidates": 0, "horizons": {}}
    selected = [i for i in range(n-1) if math.isfinite(scores[i]) and 0 < scores[i] < threshold and math.isfinite(opens[i+1]) and opens[i+1] > 0]
    results = {}
    for h in horizons:
        obs = []
        for i in selected:
            end = i + int(h)
            if end >= n or not math.isfinite(closes[end]):
                continue
            ret = (closes[end] / opens[i+1] - 1.0) * 100.0
            if math.isfinite(ret): obs.append({"net_return_pct": ret})
        results[str(h)] = _summary(obs)
    return {"status": "retrospective_descriptive_only", "candidates": len(selected),
            "definition": f"Positive score below long threshold ({threshold:g}); next-open to horizon-close returns.",
            "horizons": results,
            "warning": "Overlapping observations; no costs, no execution simulation, and future returns are labels only. Not evidence that rejected trades should be taken."}


__all__ = ["analyze_trade_quality", "analyze_rejected_bullish_candidates"]
