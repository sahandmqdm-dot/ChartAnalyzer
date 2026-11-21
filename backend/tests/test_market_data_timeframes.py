import pytest

from app.market_data.timeframe import Timeframe


def test_timeframe_fixed_duration_seconds():
    assert Timeframe.M1.seconds == 60
    assert Timeframe.H1.seconds == 3600
    assert Timeframe.D1.seconds == 86400
    assert Timeframe.W1.seconds == 604800


def test_monthly_timeframe_is_calendar_based():
    assert Timeframe.MONTH_1.seconds is None


def test_timeframe_accepts_canonical_exchange_notation():
    assert Timeframe("1h") is Timeframe.H1
    assert Timeframe("1M") is Timeframe.MONTH_1


def test_unknown_timeframe_is_rejected():
    with pytest.raises(ValueError):
        Timeframe("17m")
