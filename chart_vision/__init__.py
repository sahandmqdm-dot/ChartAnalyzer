"""Optional visual chart input: detections are research candidates, not decisions."""
from .detector import detect_patterns
from .image_input import decode_image, inspect_image
from .mapping import map_detection_to_ohlcv
from .pipeline import analyze_chart_upload
from .signals import candidate_signal

__all__ = ["analyze_chart_upload", "candidate_signal", "decode_image", "detect_patterns", "inspect_image", "map_detection_to_ohlcv"]
