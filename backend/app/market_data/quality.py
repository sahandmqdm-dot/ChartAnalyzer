from itertools import pairwise
from collections.abc import Sequence

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
