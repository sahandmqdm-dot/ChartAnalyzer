from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.market_data.candle import Candle
from app.market_data.quality import validate_candle_series


def make_candle(minute: int, *, open_: str = "10", high: str = "12", low: str = "9", close: str = "11", volume: str = "5"):
    return Candle(
        timestamp=datetime(2026, 1, 1, 0, minute, tzinfo=timezone.utc),
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
    )


def test_candle_accepts_valid_ohlc():
    candle = make_candle(0)
    assert candle.high == Decimal("12")
    assert candle.timestamp.tzinfo is not None


def test_candle_rejects_invalid_ohlc():
    with pytest.raises(ValidationError):
        make_candle(1, high="10.5", open_="11", close="11.2")


def test_candle_requires_timezone():
    with pytest.raises(ValidationError):
        Candle(timestamp=datetime(2026, 1, 1), open=1, high=2, low=0.5, close=1.5, volume=0)


def test_candle_volume_cannot_be_negative():
    with pytest.raises(ValidationError):
        make_candle(2, volume="-1")


def test_series_requires_increasing_time():
    with pytest.raises(ValueError, match="strictly increasing"):
        validate_candle_series([make_candle(2), make_candle(1)])


def test_series_rejects_duplicate_time():
    with pytest.raises(ValueError, match="strictly increasing"):
        validate_candle_series([make_candle(3), make_candle(3)])


def test_series_valid():
    assert len(validate_candle_series([make_candle(0), make_candle(1)])) == 2
