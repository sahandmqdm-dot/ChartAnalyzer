"""Exit Path Intelligence for frozen-signal OOS trade audits.

Measures how much favorable excursion was retained at the actual exit and when
that excursion occurred. OHLC ordering remains unknown; this is descriptive,
not an alternative executable exit strategy.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, Iterable, Optional

import numpy as np


def _finite(value: Any) -> Optional[float]:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _ts(df, i: int):
    """Use the actual OHLCV timestamp column when present."""
    try:
        if "time" in df.columns:
            x = float(df["time"].iloc[i])
            if math.isfinite(x):
                from datetime import datetime, timezone
                return datetime.fromtimestamp(x, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        x = df.index[i]
        return x.isoformat() if hasattr(x, "isoformat") else str(x)
    except Exception:
        return None


def analyze_exit_paths(trades: Iterable[Dict[str, Any]], df) -> Dict[str, Any]:
    """Summarize favorable-excursion giveback for completed trades.

    Entry candle is included. Signal/reversal exits are filled at exit open and
    exclude the exit candle from excursion calculations. Stop/target exits are
    intrabar and include the exit candle. Returns are price-direction returns,
    not account returns and not net of costs.
    """
    try:
        opens = np.asarray(df["open"], dtype=float)
        highs = np.asarray(df["high"], dtype=float)
        lows = np.asarray(df["low"], dtype=float)
        closes = np.asarray(df["close"], dtype=float)
    except Exception as exc:
        return {"status": "unavailable", "reason": f"OHLC unavailable: {exc}", "trades": []}
    n = min(map(len, (opens, highs, lows, closes)))
    if n < 2 or any(not np.isfinite(x[:n]).all() for x in (opens, highs, lows, closes)):
        return {"status": "unavailable", "reason": "Insufficient or non-finite OHLC data", "trades": []}

    rows = []
    by_exit = defaultdict(list)
    valid = invalid = losers_with_mfe = 0
    total_mfe = total_giveback = 0.0
    winner_capture_sum = 0.0
    winner_capture_n = giveback_n = 0

    for ordinal, trade in enumerate(trades or [], 1):
        if not isinstance(trade, dict):
            invalid += 1
            continue
        try:
            entry = int(trade.get("global_entry_bar", trade.get("entry_bar", -1)))
            exit_ = int(trade.get("global_exit_bar", trade.get("exit_bar", -1)))
        except (TypeError, ValueError):
            invalid += 1
            continue
        entry_px = _finite(trade.get("entry_px"))
        exit_px = _finite(trade.get("exit_px"))
        if entry_px is None or exit_px is None or entry_px <= 0 or exit_px <= 0 or not (0 <= entry <= exit_ < n):
            invalid += 1
            continue

        side = 1 if str(trade.get("side", "long")).lower() == "long" else -1
        reason = str(trade.get("exit_reason", "unknown"))
        path_end = exit_ + 1 if reason in {"stop_loss", "take_profit"} else exit_
        path_end = max(entry + 1, min(path_end, n))
        bars = np.arange(entry, path_end, dtype=int)
        if not len(bars):
            invalid += 1
            continue

        favorable_high = (highs[bars] / entry_px - 1.0) * side
        favorable_low = (lows[bars] / entry_px - 1.0) * side
        bar_mfe = np.maximum(favorable_high, favorable_low)
        peak_offset = int(np.argmax(bar_mfe))
        peak_bar = int(bars[peak_offset])
        mfe = max(0.0, float(bar_mfe[peak_offset]))
        realized_gross = (exit_px / entry_px - 1.0) * side
        giveback = mfe - realized_gross
        capture = realized_gross / mfe if mfe > 1e-12 else None

        peak_close_return = (closes[peak_bar] / entry_px - 1.0) * side
        post_peak_bars = bars[peak_offset:]
        post_peak_close_returns = (closes[post_peak_bars] / entry_px - 1.0) * side
        max_close_giveback = max(0.0, mfe - float(np.min(post_peak_close_returns))) if len(post_peak_close_returns) else 0.0
        close_recovered_at_exit = (closes[post_peak_bars[-1]] / entry_px - 1.0) * side if len(post_peak_bars) else None
        held = max(0, exit_ - entry)
        peak_age = max(0, exit_ - peak_bar)
        peak_phase = "early" if held and peak_age / held >= 0.66 else ("late" if held and peak_age / held <= 0.33 else "middle")
        net = _finite(trade.get("net_ret"))
        if net is not None and net < 0 and mfe > 0:
            losers_with_mfe += 1

        row = {
            "trade_index": ordinal,
            "side": trade.get("side", "long"),
            "entry_bar": entry,
            "exit_bar": exit_,
            "entry_timestamp": _ts(df, entry),
            "exit_timestamp": _ts(df, exit_),
            "exit_reason": reason,
            "bars_held": held,
            "mfe_pct": round(mfe * 100.0, 4),
            "mfe_peak_bar": peak_bar,
            "mfe_peak_timestamp": _ts(df, peak_bar),
            "bars_from_mfe_peak_to_exit": peak_age,
            "mfe_peak_phase": peak_phase,
            "peak_bar_close_return_pct": round(peak_close_return * 100.0, 4),
            "max_post_peak_close_giveback_pct": round(max_close_giveback * 100.0, 4),
            "realized_gross_price_return_pct": round(realized_gross * 100.0, 4),
            "favorable_excursion_giveback_pct": round(giveback * 100.0, 4),
            "gross_mfe_capture_ratio": round(capture, 4) if capture is not None else None,
            "net_account_return_pct": round(net * 100.0, 4) if net is not None else None,
            "classification": "loss_after_positive_mfe" if net is not None and net < 0 and mfe > 0 else ("positive_exit_after_mfe" if realized_gross > 0 and mfe > 0 else "no_positive_mfe"),
            "path_note": "OHLC identifies the peak candle but not intrabar event order; realized return uses actual recorded exit fill. Descriptive only.",
        }
        rows.append(row)
        valid += 1
        by_exit[reason].append(row)
        total_mfe += mfe * 100.0
        if mfe > 1e-12:
            total_giveback += giveback * 100.0
            giveback_n += 1
            # A capture ratio is interpretable as retained favorable movement only
            # for a positive gross exit. Keep losing trades in their own cohort.
            if realized_gross > 0 and capture is not None and math.isfinite(capture):
                winner_capture_sum += capture
                winner_capture_n += 1

    def summarize(group):
        if not group:
            return {"trades": 0}
        caps = [r["gross_mfe_capture_ratio"] for r in group
                if r["mfe_pct"] > 0 and r["realized_gross_price_return_pct"] > 0
                and r["gross_mfe_capture_ratio"] is not None]
        signed_caps = [r["gross_mfe_capture_ratio"] for r in group
                       if r["mfe_pct"] > 0 and r["gross_mfe_capture_ratio"] is not None]
        givebacks = [r["favorable_excursion_giveback_pct"] for r in group if r["mfe_pct"] > 0]
        return {
            "trades": len(group),
            "positive_mfe_trades": sum(r["mfe_pct"] > 0 for r in group),
            "losers_with_positive_mfe": sum(r["classification"] == "loss_after_positive_mfe" for r in group),
            "mean_mfe_pct": round(float(np.mean([r["mfe_pct"] for r in group])), 4),
            "mean_giveback_pct": round(float(np.mean(givebacks)), 4) if givebacks else None,
            "median_gross_mfe_capture_ratio": round(float(np.median(caps)), 4) if caps else None,
            "gross_mfe_capture_ratio_n": len(caps),
            "median_signed_realized_to_mfe_ratio": round(float(np.median(signed_caps)), 4) if signed_caps else None,
            "mean_bars_from_peak_to_exit": round(float(np.mean([r["bars_from_mfe_peak_to_exit"] for r in group])), 2),
        }

    return {
        "version": "1.1",
        "status": "descriptive_only" if valid else "no_valid_trades",
        "trades_analyzed": valid,
        "trades_invalid": invalid,
        "overall": {
            "mean_mfe_pct": round(total_mfe / valid, 4) if valid else None,
            "mean_giveback_pct": round(total_giveback / giveback_n, 4) if giveback_n else None,
            "mean_gross_mfe_capture_ratio": round(winner_capture_sum / winner_capture_n, 4) if winner_capture_n else None,
            "median_gross_mfe_capture_ratio": round(float(np.median([r["gross_mfe_capture_ratio"] for r in rows if r["mfe_pct"] > 0 and r["realized_gross_price_return_pct"] > 0 and r["gross_mfe_capture_ratio"] is not None])), 4) if winner_capture_n else None,
            "gross_mfe_capture_ratio_n": winner_capture_n,
            "losers_with_positive_mfe": losers_with_mfe,
        },
        "by_exit_reason": {k: summarize(v) for k, v in sorted(by_exit.items())},
        "trades": rows,
        "guardrails": [
            "MFE is an intrabar excursion, not a guaranteed executable fill.",
            "OHLC cannot establish the order of high/low events within a candle.",
            "Capture and giveback are descriptive and do not validate an alternative exit rule.",
            "Returns are gross price-direction returns; account costs and sizing are not applied to this path metric.",
        ],
    }


__all__ = ["analyze_exit_paths"]
