"""Descriptive signal-level diagnostics for frozen walk-forward trades.

This module does not change signals, optimize parameters, or infer causality.
OHLC excursions are range-based; they do not reveal the order of intrabar highs/lows.
"""
from __future__ import annotations
from collections import Counter
from typing import Any, Dict, Iterable
import numpy as np


def _num(value):
    try:
        x = float(value)
        return x if np.isfinite(x) else None
    except (TypeError, ValueError, OverflowError):
        return None


def build_signal_forensics(trades: Iterable[Dict[str, Any]], df, *, threshold: float, restricted_hours_enabled: bool = False) -> Dict[str, Any]:
    rows = []
    exit_counts = Counter()
    regime_counts = Counter()
    conviction = Counter()
    component_keys = sorted({k for t in (trades or []) if isinstance(t, dict) for k in t if k.startswith("entry_component_")})
    for index, trade in enumerate(trades or [], 1):
        if not isinstance(trade, dict):
            continue
        entry = int(trade.get("global_entry_bar", -1))
        exit_ = int(trade.get("global_exit_bar", -1))
        side = 1 if str(trade.get("side", "long")).lower() == "long" else -1
        entry_px = _num(trade.get("entry_px"))
        hi_exc = lo_exc = None
        if entry_px and 0 <= entry < len(df) and 0 <= exit_ < len(df) and exit_ >= entry:
            # Entry candle through the candle before exit. Exit is filled at its open.
            # ATR stop/target fills occur inside the exit candle; signal exits
            # fill at the exit candle open and must exclude that candle's range.
            exit_reason = str(trade.get("exit_reason", "unknown"))
            end = exit_ + 1 if exit_reason in {"stop_loss", "take_profit"} else exit_
            end = max(entry + 1, min(end, len(df)))
            window = df.iloc[entry:end]
            if len(window):
                highs = np.asarray(window["high"], dtype=float)
                lows = np.asarray(window["low"], dtype=float)
                hi_exc = float(np.max((highs / entry_px - 1.0) * side))
                lo_exc = float(np.min((lows / entry_px - 1.0) * side))
        score = _num(trade.get("entry_signal_score"))
        band = "unknown" if score is None else ("near_threshold" if abs(score) < threshold * 1.25 else "high_conviction")
        conviction[band] += 1
        exit_reason = str(trade.get("exit_reason", "unknown"))
        exit_counts[exit_reason] += 1
        regime = str(trade.get("entry_regime", "unknown"))
        regime_counts[regime] += 1
        components = {k.removeprefix("entry_component_"): _num(trade.get(k)) for k in component_keys}
        valid_components = [v for v in components.values() if v is not None]
        positive = sum(v > 0 for v in valid_components)
        negative = sum(v < 0 for v in valid_components)
        net = _num(trade.get("net_ret"))
        rows.append({
            "trade_index": index, "entry_bar": entry, "exit_bar": exit_,
            "entry_signal_score": score, "entry_conviction": band,
            "entry_regime": regime, "side": trade.get("side"),
            "net_return_pct": round(net * 100, 4) if net is not None else None,
            "exit_reason": exit_reason, "bars_held": trade.get("bars_held"),
            "intrabar_mfe_pct": round(hi_exc * 100, 4) if hi_exc is not None else None,
            "intrabar_mae_pct": round(lo_exc * 100, 4) if lo_exc is not None else None,
            "component_positive_votes": positive, "component_negative_votes": negative,
            "component_scores": components,
            "entry_fill_allowed": trade.get("entry_fill_allowed"),
        })
    n = len(rows)
    losses = [r for r in rows if r["net_return_pct"] is not None and r["net_return_pct"] < 0]
    missed_move = [r for r in losses if r["intrabar_mfe_pct"] is not None and r["intrabar_mfe_pct"] > 0]
    return {
        "status": "descriptive_only", "trades_analyzed": n,
        "by_exit_reason": dict(exit_counts), "by_entry_regime": dict(regime_counts),
        "by_entry_conviction": dict(conviction),
        "losing_trades_with_positive_intrabar_mfe": len(missed_move),
        "losing_trades_with_positive_intrabar_mfe_pct": round(100 * len(missed_move) / len(losses), 2) if losses else None,
        "restricted_hours_filter_configured": bool(restricted_hours_enabled),
        "entries_blocked_by_hours": sum(r.get("entry_fill_allowed") is False for r in rows),
        "trades": rows,
        "method": "OHLC range excursion from entry fill through pre-exit candles; stop/target ordering within a candle is unknown.",
        "warning": "Component votes and excursions are descriptive diagnostics, not calibrated probabilities or proof of causation. No parameters were optimized.",
    }
