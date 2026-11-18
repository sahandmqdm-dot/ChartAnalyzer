"""Align image boxes to candles only with explicit user-supplied chart calibration."""
from __future__ import annotations
import math
import numpy as np


def map_detection_to_ohlcv(detection, calibration, df, *, image_width: int, image_height: int):
    """Return inclusive candle indices and price bounds; this is alignment, not pattern confirmation.

    Calibration requires plot_bounds=[left,top,right,bottom] in pixels and epoch-second
    start_timestamp/end_timestamp plus linear price_min/price_max for the visible plot.
    Log-scaled axes are not supported.
    """
    if not isinstance(calibration, dict):
        raise ValueError("chart_mapping must be an object")
    try:
        left, top, right, bottom = map(float, calibration["plot_bounds"])
        start, end = float(calibration["start_timestamp"]), float(calibration["end_timestamp"])
        pmin, pmax = float(calibration["price_min"]), float(calibration["price_max"])
        x1, y1, x2, y2 = map(float, detection["bbox_xyxy"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("mapping/detection coordinates are missing or invalid") from exc
    vals = (left, top, right, bottom, start, end, pmin, pmax, x1, y1, x2, y2)
    if not all(math.isfinite(x) for x in vals):
        raise ValueError("mapping coordinates must be finite")
    if not (0 <= left < right <= image_width and 0 <= top < bottom <= image_height):
        raise ValueError("plot_bounds must be inside image bounds")
    if not (start < end and 0 < pmin < pmax and x1 < x2 and y1 < y2):
        raise ValueError("time, price and box bounds must be increasing and valid")
    bx1, by1, bx2, by2 = max(left, x1), max(top, y1), min(right, x2), min(bottom, y2)
    if bx1 >= bx2 or by1 >= by2:
        raise ValueError("detection does not overlap calibrated plot")
    if "time" not in df.columns or len(df) == 0:
        raise ValueError("OHLCV frame is empty or missing time")
    times = np.asarray(df["time"], dtype=float)
    if not np.isfinite(times).all() or np.any(np.diff(times) <= 0):
        raise ValueError("OHLCV timestamps must be finite, sorted and unique")
    t0 = start + (bx1-left)/(right-left)*(end-start)
    t1 = start + (bx2-left)/(right-left)*(end-start)
    if t1 < times[0] or t0 > times[-1]:
        raise ValueError("detected time interval does not overlap OHLCV history")
    first = max(0, int(np.searchsorted(times, t0, side="left")))
    last = min(len(times)-1, int(np.searchsorted(times, t1, side="right")-1))
    if last < first:
        raise ValueError("detected interval contains no candle timestamps")
    price_at_top = pmax - (by1-top)/(bottom-top)*(pmax-pmin)
    price_at_bottom = pmax - (by2-top)/(bottom-top)*(pmax-pmin)
    return {"start_timestamp": float(t0), "end_timestamp": float(t1),
            "price_low": float(min(price_at_top, price_at_bottom)),
            "price_high": float(max(price_at_top, price_at_bottom)),
            "first_candle_index": first, "last_candle_index": last,
            "event_candle_index": last, "matched_candles": last-first+1,
            "alignment": "EXPLICIT_USER_CALIBRATION",
            "note": "Coordinate alignment only; OHLCV pattern confirmation and predictive edge are not established."}
