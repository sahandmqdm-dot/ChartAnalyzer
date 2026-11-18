"""Lazy Ultralytics YOLO inference; trusted weights are configured locally, never uploaded."""
from __future__ import annotations
import hashlib
import math
import os
from pathlib import Path
from typing import Any
from io import BytesIO


def _identity(path: str):
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise ValueError("configured YOLO weights file does not exist")
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return {"filename": p.name, "sha256": h.hexdigest()}


def detect_patterns(image_bytes: bytes, *, model_path: str | None = None, predictor=None,
                    confidence: float = 0.25, max_detections: int = 50):
    from PIL import Image
    if not math.isfinite(float(confidence)) or not 0 < float(confidence) <= 1:
        raise ValueError("confidence must be in (0, 1]")
    if not 1 <= int(max_detections) <= 100:
        raise ValueError("max_detections must be in [1, 100]")
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    path = model_path or os.getenv("CHARTANALYZER_YOLO_MODEL", "")
    model = {"filename": "injected-test-predictor", "sha256": "unavailable"}
    if predictor is None:
        if not path:
            raise RuntimeError("configure CHARTANALYZER_YOLO_MODEL with trusted YOLO weights")
        model = _identity(path)
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError("install optional requirements-vision.txt dependencies") from exc
        predictor = YOLO(path)
    results = predictor.predict(source=image, conf=float(confidence), max_det=int(max_detections), verbose=False)
    if not results:
        return []
    result, output = results[0], []
    names = getattr(result, "names", {}) or {}
    for box in getattr(result, "boxes", []) or []:
        try:
            cls = int(box.cls[0].item())
            score = float(box.conf[0].item())
            coords = [float(x) for x in box.xyxy[0].tolist()]
        except (AttributeError, IndexError, TypeError, ValueError) as exc:
            raise ValueError("unsupported YOLO result format") from exc
        if len(coords) != 4 or not all(math.isfinite(x) for x in coords) or not math.isfinite(score):
            continue
        label = names.get(cls, str(cls)) if isinstance(names, dict) else str(cls)
        pattern = str(label).strip().lower().replace(" ", "_").replace("-", "_")[:64]
        direction = {"w_bottom": "bullish", "double_bottom": "bullish",
                     "head_and_shoulders": "bearish", "inverse_head_and_shoulders": "bullish"}.get(pattern, "unknown")
        output.append({"pattern": pattern, "confidence": min(1.0, max(0.0, score)),
                       "direction": direction, "bbox_xyxy": coords, "model": model})
    return output
