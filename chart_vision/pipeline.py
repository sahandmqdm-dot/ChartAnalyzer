"""Framework-independent upload orchestration using the application's OHLCV loader."""
from __future__ import annotations
import math
from typing import Callable

from data_integrity import audit_ohlcv
from .detector import detect_patterns
from .image_input import decode_image, inspect_image
from .mapping import map_detection_to_ohlcv
from .signals import candidate_signal


def analyze_chart_upload(payload: dict, *, ohlcv_loader: Callable, timeframe_seconds: int,
                         model_path: str | None = None, predictor=None,
                         max_image_bytes: int = 3 * 1024 * 1024):
    """Detect, fetch the selected OHLCV sample, audit, and map to a candidate signal.

    Intended integration: inject the existing ``app.load_cached`` as
    ``ohlcv_loader``. It never writes image bytes or mixes image/live data into
    historical research storage.
    """
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    missing = [k for k in ("image_base64", "source", "symbol", "tf") if not payload.get(k)]
    if missing:
        raise ValueError("missing required fields: " + ", ".join(missing))
    confidence = float(payload.get("confidence", 0.25))
    if not math.isfinite(confidence) or not 0 < confidence <= 1:
        raise ValueError("confidence must be in (0, 1]")
    image_bytes = decode_image(payload["image_base64"], max_bytes=max_image_bytes)
    image = inspect_image(image_bytes)
    detections = detect_patterns(image_bytes, model_path=model_path, predictor=predictor,
                                 confidence=confidence,
                                 max_detections=max(1, min(int(payload.get("max_detections", 50)), 100)))
    source, symbol, tf = (str(payload[k]).strip() for k in ("source", "symbol", "tf"))
    df = ohlcv_loader(source=source, symbol=symbol, tf=tf,
                      n=max(100, min(int(payload.get("n", 500)), 5000)),
                      exchange=str(payload.get("exchange", "coinex")),
                      udf_url=str(payload.get("udf_url", "")),
                      csv_path=str(payload.get("csv_path", "")))
    audit = audit_ohlcv(df, int(timeframe_seconds), check_stale=source in ("ramzinex", "udf", "ccxt"))
    out = {"source": source, "symbol": symbol, "timeframe": tf, "image": image,
           "detections": detections, "data_integrity": audit,
           "candidate_signals": [], "status": "CHART_CALIBRATION_REQUIRED"}
    calibration = payload.get("chart_mapping")
    if not calibration:
        out["policy"] = "Detections are visual candidates only. Explicit chart mapping is required to align them to fetched OHLCV. No trade decision was made."
        return out
    if not audit.get("valid", False):
        out.update(status="BLOCKED_OHLCV_INTEGRITY",
                   policy="OHLCV integrity failure blocks pattern mapping and all further evaluation.")
        return out
    candidates, errors = [], []
    for detection in detections:
        try:
            window = map_detection_to_ohlcv(detection, calibration, df,
                                            image_width=image["width"], image_height=image["height"])
            candidates.append({**detection, "ohlcv_window": window,
                               "signal_role": "RESEARCH_CANDIDATE_ONLY"})
        except ValueError as exc:
            errors.append({"pattern": detection["pattern"], "reason": str(exc)})
    built = candidate_signal(len(df), candidates)
    out.update(status="OHLCV_ALIGNED_CANDIDATES_NOT_STATISTICALLY_VALIDATED",
               candidate_signals=candidates, signal=built["signal"].tolist(),
               signal_events=built["events"], mapping_errors=errors,
               policy="The endpoint verifies data integrity and coordinate alignment only; it does not confirm OHLCV morphology or predictive edge. Pass a fixed, pre-registered OHLCV rule through existing bootstrap/FDR/chronological OOS research before any deployment gate can permit use.")
    return out
