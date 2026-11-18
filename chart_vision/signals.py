"""Convert mapped pattern candidates into explicit sparse research signals."""
from __future__ import annotations
import numpy as np


def candidate_signal(candles: int, candidates, *, allowed_patterns=None):
    """Create a sparse direction score at each candidate event candle.

    The signal uses direction only, not detector confidence as a probability.
    It is an experimental input for a separately specified, pre-registered
    research hypothesis; it does not define exits, trading eligibility, or gates.
    """
    n = int(candles)
    if n < 1:
        raise ValueError("candles must be positive")
    allow = None if allowed_patterns is None else {str(x).lower() for x in allowed_patterns}
    signal = np.zeros(n, dtype=float)
    events = []
    for item in candidates:
        pattern = str(item.get("pattern", "")).lower()
        if allow is not None and pattern not in allow:
            continue
        idx = int(item["ohlcv_window"]["event_candle_index"])
        direction = item.get("direction")
        if not 0 <= idx < n:
            raise ValueError("candidate event index is outside the candle series")
        if direction not in ("bullish", "bearish"):
            continue
        score = 1.0 if direction == "bullish" else -1.0
        # Conflicting same-bar detections are neutralized instead of arbitrarily chosen.
        if signal[idx] and signal[idx] != score:
            signal[idx] = 0.0
            events.append({"pattern": pattern, "event_candle_index": idx, "status": "CONFLICT_NEUTRALIZED"})
        elif signal[idx] == 0.0:
            signal[idx] = score
            events.append({"pattern": pattern, "event_candle_index": idx, "status": "CANDIDATE_ONLY"})
    return {"signal": signal, "events": events,
            "status": "RESEARCH_CANDIDATE_NOT_VALIDATED",
            "policy": "Sparse pattern event scores are not probabilities or trade instructions; validate a frozen, pre-registered rule using existing OHLCV bootstrap, FDR and chronological OOS gates."}
