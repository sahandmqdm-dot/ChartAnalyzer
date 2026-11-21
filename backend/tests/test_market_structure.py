from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.market_data.mark_price import MarkPriceCandle
from app.market_data.market_structure import analyze_market_structure


def _candle(index: int, opening: str, high: str, low: str, close: str, *, closed=True):
    timestamp = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=index)
    return MarkPriceCandle(
        timestamp=timestamp,
        close_time=timestamp + timedelta(minutes=59),
        is_closed=closed,
        open=Decimal(opening),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
    )


def test_market_structure_ignores_unclosed_candles():
    candles = [
        _candle(i, str(100 + i), str(101 + i), str(99 + i), str(100 + i))
        for i in range(10)
    ]
    candles.append(_candle(10, "110", "500", "1", "400", closed=False))
    result = analyze_market_structure(
        symbol="BTCUSDT", period="1h", provider="test",
        candles=candles, observed_at=datetime(2026, 1, 2, tzinfo=UTC),
        pivot_window=2,
    )
    assert result.closed_candles == 10
    assert result.latest_close == Decimal("109")


def test_market_structure_detects_fair_value_gap():
    candles = [
        _candle(0, "100", "102", "99", "101"),
        _candle(1, "102", "106", "101", "105"),
        _candle(2, "107", "109", "104", "108"),
        _candle(3, "108", "110", "107", "109"),
        _candle(4, "109", "111", "108", "110"),
        _candle(5, "110", "112", "109", "111"),
        _candle(6, "111", "113", "110", "112"),
    ]
    result = analyze_market_structure(
        symbol="BTCUSDT", period="1h", provider="test",
        candles=candles, observed_at=datetime(2026, 1, 2, tzinfo=UTC),
        pivot_window=2,
    )
    assert any(gap.direction == "bullish" for gap in result.fair_value_gaps)


def test_market_structure_requires_sufficient_closed_candles():
    result = analyze_market_structure(
        symbol="BTCUSDT", period="1h", provider="test",
        candles=[_candle(i, "100", "101", "99", "100") for i in range(5)],
        observed_at=datetime(2026, 1, 2, tzinfo=UTC), pivot_window=3,
    )
    assert result.trend == "insufficient_data"
    assert result.swings == []


def test_market_structure_rejects_invalid_pivot_window():
    with pytest.raises(ValueError):
        analyze_market_structure(
            symbol="BTCUSDT", period="1h", provider="test", candles=[],
            observed_at=datetime(2026, 1, 2, tzinfo=UTC), pivot_window=1,
        )
