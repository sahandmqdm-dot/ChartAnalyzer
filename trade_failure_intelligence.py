"""Per-trade failure reconstruction and exit-path research.

All outputs are descriptive or explicitly labelled counterfactuals. This module
never changes signals, tunes parameters, or claims executable profitability.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

import numpy as np

from version import APP_VERSION


def _f(value: Any) -> float | None:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _timestamp(df, index: int):
    """Return the candle timestamp, not the DataFrame's usually-integer row index."""
    try:
        if "time" in df.columns:
            value = float(df["time"].iloc[index])
            if math.isfinite(value):
                return datetime.fromtimestamp(value, tz=UTC).isoformat().replace("+00:00", "Z")
        value = df.index[index]
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    except Exception:
        return None


def _side(trade: dict[str, Any]) -> int:
    return 1 if str(trade.get("side", "long")).lower() == "long" else -1


def _net_after_costs(gross_asset_return: float, exposure: float, fee: float,
                     spread_rt: float, funding_per_bar: float, bars: int,
                     market_impact_rt: float = 0.0) -> float | None:
    """Mirror the engine's multiplicative cost convention for a hypothetical exit."""
    try:
        if exposure <= 0 or fee < 0 or spread_rt < 0 or market_impact_rt < 0 or not math.isfinite(funding_per_bar):
            return None
        gross_factor = 1.0 + exposure * gross_asset_return
        if gross_factor <= 0:
            return -1.0
        fee_factor = (1.0 - exposure * fee) ** 2
        spread_factor = (1.0 - exposure * (spread_rt + market_impact_rt) / 2.0) ** 2
        funding_base = 1.0 - exposure * funding_per_bar
        if funding_base <= 0:
            return None
        funding_factor = funding_base ** max(0, bars)
        factor = gross_factor * fee_factor * spread_factor * funding_factor
        return factor - 1.0 if math.isfinite(factor) else None
    except Exception:
        return None


def _first_level_touch(highs, lows, level: float | None, side: int, kind: str):
    if level is None or not math.isfinite(level) or level <= 0:
        return None
    for offset, (hi, lo) in enumerate(zip(highs, lows)):
        if kind == "stop":
            touched = lo <= level if side > 0 else hi >= level
        else:
            touched = hi >= level if side > 0 else lo <= level
        if touched:
            return offset
    return None


def _counterfactual_time_exit(opens, highs, lows, entry: int, horizon: int,
                               entry_px: float, side: int,
                               stop_px: float | None, target_px: float | None):
    """Apply existing ATR barriers before a requested next-open time exit."""
    for bar in range(entry, entry + horizon):
        hi, lo, op = float(highs[bar]), float(lows[bar]), float(opens[bar])
        stop_hit = stop_px is not None and (lo <= stop_px if side > 0 else hi >= stop_px)
        target_hit = target_px is not None and (hi >= target_px if side > 0 else lo <= target_px)
        if stop_hit:
            fill = min(op, stop_px) if side > 0 else max(op, stop_px)
            return {"exit_global_bar": bar, "exit_price": float(fill),
                    "exit_reason": "stop_loss", "effective_holding_bars": bar - entry,
                    "same_bar_stop_target_ambiguous": bool(target_hit)}
        if target_hit:
            fill = max(op, target_px) if side > 0 else min(op, target_px)
            return {"exit_global_bar": bar, "exit_price": float(fill),
                    "exit_reason": "take_profit", "effective_holding_bars": bar - entry,
                    "same_bar_stop_target_ambiguous": False}
    return {"exit_global_bar": entry + horizon, "exit_price": float(opens[entry + horizon]),
            "exit_reason": "time_exit", "effective_holding_bars": horizon,
            "same_bar_stop_target_ambiguous": False}


def analyze_trade_failures(
    trades: Iterable[dict[str, Any]], df, *, signal=None, entry_regime=None,
    entry_components: dict[str, Any] | None = None, fee_per_side: float = 0.0,
    spread_slippage_rt: float = 0.0, market_impact_rt: float = 0.0,
    funding_fee_per_bar: float = 0.0,
    time_exit_horizons=(1, 2, 3, 6, 12), favorable_thresholds=(0.25, 0.5, 1.0),
) -> dict[str, Any]:
    """Reconstruct each trade path and compare earlier time exits descriptively.

    Trade indices are global candle indices from walk_forward. Entry is at the
    entry candle open. Signal/reversal exits occur at exit candle open, so that
    candle is excluded from their pre-exit path. ATR stop/target exits occur
    inside the exit candle, which is included. If both levels touch in a candle,
    the event is marked ambiguous and stop-first, matching the engine's rule.
    """
    try:
        opens = np.asarray(df["open"], dtype=float)
        highs = np.asarray(df["high"], dtype=float)
        lows = np.asarray(df["low"], dtype=float)
        closes = np.asarray(df["close"], dtype=float)
    except Exception as exc:
        return {"version": APP_VERSION, "status": "unavailable", "reason": f"OHLC unavailable: {exc}"}
    n = min(len(opens), len(highs), len(lows), len(closes))
    if n < 2 or not (np.isfinite(opens[:n]).all() and np.isfinite(highs[:n]).all()
                     and np.isfinite(lows[:n]).all() and np.isfinite(closes[:n]).all()):
        return {"version": APP_VERSION, "status": "unavailable", "reason": "OHLC contains insufficient or non-finite data."}

    sig = None if signal is None else np.asarray(signal, dtype=float)
    regimes = None if entry_regime is None else np.asarray(entry_regime, dtype=object)
    comps = {str(k): np.asarray(v, dtype=float) for k, v in (entry_components or {}).items()}
    if sig is not None and len(sig) != n:
        raise ValueError("signal length must match OHLC data")
    if regimes is not None and len(regimes) != n:
        raise ValueError("entry_regime length must match OHLC data")
    if any(len(v) != n for v in comps.values()):
        raise ValueError("each entry component length must match OHLC data")

    rows = []
    tags_count = Counter()
    horizon_rows = defaultdict(list)
    exit_counts = Counter()
    valid_trade_count = 0

    for ordinal, trade in enumerate(trades or [], 1):
        if not isinstance(trade, dict):
            continue
        entry = int(trade.get("global_entry_bar", trade.get("entry_bar", -1)))
        exit_ = int(trade.get("global_exit_bar", trade.get("exit_bar", -1)))
        entry_px = _f(trade.get("entry_px"))
        exit_px = _f(trade.get("exit_px"))
        if entry_px is None or entry_px <= 0 or not (0 <= entry < n and 0 <= exit_ < n and exit_ >= entry):
            rows.append({"trade_index": ordinal, "audit_status": "invalid_trade_indices_or_price"})
            continue
        valid_trade_count += 1
        side = _side(trade)
        reason = str(trade.get("exit_reason", "unknown"))
        # Intrabar exits are filled inside the exit candle; signal exits fill at its open.
        path_end = exit_ + 1 if reason in {"stop_loss", "take_profit"} else exit_
        path_end = max(entry + 1, min(path_end, n))
        idx = np.arange(entry, path_end, dtype=int)
        ph, pl = highs[idx], lows[idx]
        favorable = ((ph / entry_px - 1.0) * side)
        adverse = ((pl / entry_px - 1.0) * side)
        bar_fav = np.maximum(favorable, adverse)
        bar_adv = np.minimum(favorable, adverse)
        mfe = float(max(0.0, np.max(bar_fav))) if len(bar_fav) else 0.0
        mae = float(min(0.0, np.min(bar_adv))) if len(bar_adv) else 0.0
        mfe_offset = int(np.argmax(bar_fav)) if len(bar_fav) else None
        mae_offset = int(np.argmin(bar_adv)) if len(bar_adv) else None

        first_favorable = {}
        for threshold_pct in favorable_thresholds:
            threshold = float(threshold_pct) / 100.0
            found = next((j for j, value in enumerate(bar_fav) if value >= threshold), None)
            first_favorable[f"{float(threshold_pct):g}_pct"] = {
                "bars_after_entry": found,
                "global_bar": int(idx[found]) if found is not None else None,
                "timestamp": _timestamp(df, int(idx[found])) if found is not None else None,
            }

        stop_px, target_px = _f(trade.get("stop_px")), _f(trade.get("target_px"))
        stop_offset = _first_level_touch(ph, pl, stop_px, side, "stop")
        target_offset = _first_level_touch(ph, pl, target_px, side, "target")
        same_bar_both = stop_offset is not None and stop_offset == target_offset
        if same_bar_both:
            first_level_event = "ambiguous_both_stop_first"
        elif stop_offset is not None and (target_offset is None or stop_offset < target_offset):
            first_level_event = "stop_first"
        elif target_offset is not None:
            first_level_event = "target_first"
        else:
            first_level_event = "neither_touched_before_exit"

        entry_signal_bar = int(trade.get("global_entry_signal_bar", max(0, entry - 1)))
        exit_signal_bar = max(0, exit_ - 1)
        entry_score = _f(trade.get("entry_signal_score"))
        exit_score = _f(sig[exit_signal_bar]) if sig is not None and exit_signal_bar < n else None
        if entry_score is None and sig is not None and 0 <= entry_signal_bar < n:
            entry_score = _f(sig[entry_signal_bar])
        component_entry = {}
        component_exit = {}
        component_delta = {}
        for name, values in comps.items():
            a = _f(values[entry_signal_bar]) if 0 <= entry_signal_bar < n else None
            b = _f(values[exit_signal_bar]) if 0 <= exit_signal_bar < n else None
            component_entry[name] = a
            component_exit[name] = b
            component_delta[name] = round(b - a, 6) if a is not None and b is not None else None

        tags = []
        net = _f(trade.get("net_ret"))
        gross_account = _f(trade.get("gross_account_ret", trade.get("gross_ret")))
        if net is not None and net < 0 and mfe >= 0.002:
            tags.append("loss_after_favorable_excursion_over_0_20pct")
        if net is not None and net < 0 and mae <= -0.01:
            tags.append("loss_after_adverse_excursion_over_1pct")
        if net is not None and net < 0 and gross_account is not None and gross_account > 0:
            tags.append("costs_flipped_gross_gain_to_net_loss")
        if reason == "signal":
            tags.append("signal_exit_review")
        if reason in {"stop_loss", "take_profit"} and same_bar_both:
            tags.append("same_bar_stop_target_ambiguity")
        if reason == "signal" and first_level_event in {"stop_first", "target_first", "ambiguous_both_stop_first"}:
            tags.append("configured_atr_level_touched_before_signal_exit")
        for tag in tags:
            tags_count[tag] += 1

        # Early time exits are explicitly counterfactual. Only exits strictly
        # before the actual exit are compared, with a next-open fill convention.
        exposure = _f(trade.get("notional_exposure")) or 1.0
        counterfactuals = []
        bars_held = max(0, exit_ - entry)
        for horizon in time_exit_horizons:
            h = int(horizon)
            if h <= 0 or h >= bars_held or entry + h >= n:
                continue
            simulated = _counterfactual_time_exit(opens, highs, lows, entry, h,
                                                  entry_px, side, stop_px, target_px)
            px = float(simulated["exit_price"])
            gross = (px / entry_px - 1.0) * side
            effective_bars = int(simulated["effective_holding_bars"])
            net_est = _net_after_costs(gross, exposure, float(fee_per_side),
                                       float(spread_slippage_rt), float(funding_fee_per_bar), effective_bars,
                                       market_impact_rt=float(market_impact_rt))
            exit_bar_cf = int(simulated["exit_global_bar"])
            item = {"horizon_bars_requested": h, "effective_holding_bars": effective_bars,
                    "exit_global_bar": exit_bar_cf, "exit_timestamp": _timestamp(df, exit_bar_cf),
                    "exit_price": px, "exit_reason": simulated["exit_reason"],
                    "same_bar_stop_target_ambiguous": simulated["same_bar_stop_target_ambiguous"],
                    "gross_asset_return_pct": round(gross * 100.0, 4),
                    "estimated_net_account_return_pct": round(net_est * 100.0, 4) if net_est is not None else None,
                    "status": "counterfactual_before_actual_exit_with_existing_atr_barriers"}
            counterfactuals.append(item)
            if net_est is not None:
                horizon_rows[h].append(net_est * 100.0)

        prev_close = float(closes[exit_ - 1]) if exit_ > 0 else None
        exit_gap_pct = ((float(opens[exit_]) / prev_close - 1.0) * side * 100.0) if prev_close else None
        row = {
            "trade_index": ordinal, "audit_status": "ok", "side": trade.get("side"),
            "entry_bar": entry, "entry_timestamp": _timestamp(df, entry),
            "exit_bar": exit_, "exit_timestamp": _timestamp(df, exit_),
            "entry_price": entry_px, "exit_price": exit_px,
            "bars_held": bars_held, "exit_reason": reason,
            "entry_signal_bar": entry_signal_bar, "exit_decision_signal_bar": exit_signal_bar,
            "entry_signal_score": entry_score, "pre_exit_signal_score": exit_score,
            "signal_score_change": round(exit_score - entry_score, 6) if exit_score is not None and entry_score is not None else None,
            "entry_regime": trade.get("entry_regime", str(regimes[entry_signal_bar]) if regimes is not None and 0 <= entry_signal_bar < n else None),
            "component_entry_scores": component_entry,
            "component_pre_exit_scores": component_exit,
            "component_score_changes": component_delta,
            "intrabar_mfe_pct": round(mfe * 100.0, 4),
            "intrabar_mae_pct": round(mae * 100.0, 4),
            "mfe_peak_bar": int(idx[mfe_offset]) if mfe_offset is not None else None,
            "mae_trough_bar": int(idx[mae_offset]) if mae_offset is not None else None,
            "first_favorable_thresholds": first_favorable,
            "stop_price": stop_px, "target_price": target_px,
            "first_atr_level_event": first_level_event,
            "first_stop_touch_bar": int(idx[stop_offset]) if stop_offset is not None else None,
            "first_target_touch_bar": int(idx[target_offset]) if target_offset is not None else None,
            "same_bar_stop_target_ambiguous": bool(same_bar_both),
            "exit_open_gap_in_trade_direction_pct": round(exit_gap_pct, 4) if exit_gap_pct is not None else None,
            "gross_account_return_pct": round(gross_account * 100.0, 4) if gross_account is not None else None,
            "net_account_return_pct": round(net * 100.0, 4) if net is not None else None,
            "failure_tags": tags,
            "counterfactual_early_time_exits": counterfactuals,
            "path_definition": "Entry candle included. Signal exits fill at exit open and exclude exit candle; ATR exits occur intrabar and include exit candle.",
        }
        rows.append(row)
        exit_counts[reason] += 1

    horizon_summary = {}
    for horizon, values in sorted(horizon_rows.items()):
        arr = np.asarray(values, dtype=float)
        horizon_summary[str(horizon)] = {
            "samples": len(arr), "mean_estimated_net_return_pct": round(float(arr.mean()), 4),
            "median_estimated_net_return_pct": round(float(np.median(arr)), 4),
            "positive_pct": round(float(np.mean(arr > 0) * 100.0), 2),
            "note": "Only trades where this horizon precedes actual exit; existing ATR barriers are honored before the requested time exit.",
        }

    return {
        "version": APP_VERSION, "status": "descriptive_and_counterfactual", "trades_analyzed": valid_trade_count,
        "trades_invalid": len(rows) - valid_trade_count,
        "by_exit_reason": dict(exit_counts), "failure_tag_counts": dict(tags_count),
        "early_time_exit_scenarios": horizon_summary, "trades": rows,
        "method": "Global-index OHLC path reconstruction; entry at entry-bar open; signal exit at exit-bar open; ATR exit inside exit bar. Counterfactual time exits honor the existing ATR barriers.",
        "cost_model": {"fee_per_side": float(fee_per_side), "spread_slippage_roundtrip": float(spread_slippage_rt),
                       "market_impact_roundtrip": float(market_impact_rt),
                       "funding_fee_per_bar": float(funding_fee_per_bar)},
        "guardrails": [
            "Counterfactual exits are not executable strategy results and do not change the frozen strategy; existing ATR barriers are honored before each requested time exit.",
            "OHLC cannot reveal intrabar event order; same-bar stop/target touches are marked ambiguous and stop-first.",
            "Small samples are exploratory; no causal inference, parameter fitting, or profitability claim.",
        ],
    }


__all__ = ["analyze_trade_failures"]
