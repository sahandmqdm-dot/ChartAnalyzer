from collections.abc import Sequence
from datetime import timedelta
from itertools import pairwise

from app.market_data.candle import Candle


def validate_candle_series(candles: Sequence[Candle], *, require_strictly_increasing: bool = True) -> list[Candle]:
    """Return validated candle sequence; reject duplicate/out-of-order timestamps."""
    if not candles:
        raise ValueError("candle series cannot be empty")
    result = list(candles)
    for previous, current in pairwise(result):
        if require_strictly_increasing and current.timestamp <= previous.timestamp:
            raise ValueError("candle timestamps must be strictly increasing")
    return result


def find_missing_timestamps(candles: Sequence[Candle], interval_seconds: int) -> list:
    """Detect missing candle timestamps for a fixed interval, without filling data."""
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    series = validate_candle_series(candles)
    interval = timedelta(seconds=interval_seconds)
    missing = []
    for previous, current in pairwise(series):
        cursor = previous.timestamp + interval
        while cursor < current.timestamp:
            missing.append(cursor)
            cursor += interval
    return missing
