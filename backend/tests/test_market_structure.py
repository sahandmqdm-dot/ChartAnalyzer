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
    assert result.latest_close == Decimal(109)


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


def test_fair_value_gap_tracks_partial_mitigation_and_full_fill():
    candles = [
        _candle(0, "100", "102", "99", "101"),
        _candle(1, "103", "105", "102", "104"),
        _candle(2, "106", "108", "104", "107"),
        _candle(3, "107", "109", "103", "108"),
        _candle(4, "105", "107", "101", "102"),
        _candle(5, "102", "104", "100", "101"),
        _candle(6, "101", "103", "100", "102"),
    ]
    result = analyze_market_structure(
        symbol="BTCUSDT", period="1h", provider="test",
        candles=candles, observed_at=datetime(2026, 1, 2, tzinfo=UTC),
        pivot_window=2,
    )
    gap = next(item for item in result.fair_value_gaps if item.start_time == candles[0].timestamp)
    assert gap.direction == "bullish"
    assert gap.lower == Decimal("102")
    assert gap.upper == Decimal("104")
    assert gap.status == "filled"
    assert gap.mitigation_time == candles[4].timestamp
    assert gap.invalidation_time is None


def test_fair_value_gap_tracks_invalidation_after_close_through_far_edge():
    candles = [
        _candle(0, "100", "102", "99", "101"),
        _candle(1, "103", "105", "102", "104"),
        _candle(2, "106", "108", "104", "107"),
        _candle(3, "107", "109", "103", "108"),
        _candle(4, "101", "103", "99", "101"),
        _candle(5, "100", "102", "98", "99"),
        _candle(6, "99", "101", "97", "98"),
    ]
    result = analyze_market_structure(
        symbol="BTCUSDT", period="1h", provider="test",
        candles=candles, observed_at=datetime(2026, 1, 2, tzinfo=UTC),
        pivot_window=2,
    )
    gap = next(item for item in result.fair_value_gaps if item.start_time == candles[0].timestamp)
    assert gap.status == "invalidated"
    assert gap.invalidation_time == candles[4].timestamp


def test_market_structure_detects_and_tracks_bullish_order_block():
    candles = [
        _candle(0, "99", "101", "98", "100"),
        _candle(1, "100", "103", "99", "102"),
        _candle(2, "105", "110", "104", "108"),
        _candle(3, "104", "106", "100", "102"),
        _candle(4, "102", "104", "98", "99"),
        _candle(5, "106", "114", "105", "112"),
        _candle(6, "112", "113", "103", "108"),
        _candle(7, "108", "110", "96", "97"),
    ]
    result = analyze_market_structure(
        symbol="BTCUSDT", period="1h", provider="test",
        candles=candles, observed_at=datetime(2026, 1, 2, tzinfo=UTC),
        pivot_window=2,
    )
    block = next(item for item in result.order_blocks if item.direction == "bullish")
    assert block.start_time == candles[4].timestamp
    assert block.break_time == candles[5].timestamp
    assert block.lower == Decimal("98")
    assert block.upper == Decimal("104")
    assert block.status == "invalidated"
    assert block.mitigation_time == candles[6].timestamp
    assert block.invalidation_time == candles[7].timestamp
