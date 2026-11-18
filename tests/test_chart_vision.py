import base64
import numpy as np
import pandas as pd
import pytest

from chart_vision.image_input import decode_image
from chart_vision.mapping import map_detection_to_ohlcv
from chart_vision.signals import candidate_signal


def test_image_base64_validation_and_limit():
    with pytest.raises(ValueError, match="invalid"):
        decode_image("not base64!")
    encoded = base64.b64encode(b"x" * 32).decode()
    with pytest.raises(ValueError, match="size limit"):
        decode_image(encoded, max_bytes=16)


def test_explicit_calibration_maps_pixel_box_to_candle_window():
    df = pd.DataFrame({"time": np.arange(100, 110, dtype=float)})
    result = map_detection_to_ohlcv({"bbox_xyxy": [20, 20, 60, 80]},
        {"plot_bounds": [0, 0, 100, 100], "start_timestamp": 100, "end_timestamp": 110,
         "price_min": 10, "price_max": 20}, df, image_width=100, image_height=100)
    assert (result["first_candle_index"], result["last_candle_index"], result["matched_candles"]) == (2, 5, 4)
    assert result["price_low"] == pytest.approx(12)
    assert result["price_high"] == pytest.approx(18)


def test_mapping_rejects_bad_axis_or_out_of_plot_detection():
    df = pd.DataFrame({"time": np.arange(100, 110, dtype=float)})
    cal = {"plot_bounds": [0, 0, 100, 100], "start_timestamp": 100, "end_timestamp": 110,
           "price_min": 10, "price_max": 20}
    with pytest.raises(ValueError, match="does not overlap"):
        map_detection_to_ohlcv({"bbox_xyxy": [120, 10, 140, 30]}, cal, df, image_width=100, image_height=100)
    cal["price_min"] = 20
    with pytest.raises(ValueError, match="increasing"):
        map_detection_to_ohlcv({"bbox_xyxy": [10, 10, 50, 50]}, cal, df, image_width=100, image_height=100)


def test_candidate_signal_uses_direction_not_model_confidence_and_neutralizes_conflict():
    a = {"pattern": "w_bottom", "direction": "bullish", "confidence": 0.54,
         "ohlcv_window": {"event_candle_index": 3}}
    b = {"pattern": "head_and_shoulders", "direction": "bearish", "confidence": 0.99,
         "ohlcv_window": {"event_candle_index": 3}}
    out = candidate_signal(8, [a])
    assert out["signal"][3] == 1.0 and set(out["signal"]) <= {-1.0, 0.0, 1.0}
    conflict = candidate_signal(8, [a, b])
    assert conflict["signal"][3] == 0.0
    assert conflict["events"][0]["status"] == "CONFLICT_NEUTRALIZED"


def test_candidate_signal_rejects_out_of_range_index():
    with pytest.raises(ValueError, match="outside"):
        candidate_signal(3, [{"pattern": "w_bottom", "direction": "bullish",
                              "ohlcv_window": {"event_candle_index": 3}}])
