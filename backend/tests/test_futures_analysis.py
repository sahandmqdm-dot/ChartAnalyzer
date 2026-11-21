from datetime import UTC, datetime
from decimal import Decimal

from app.market_data.candle import Candle
from app.market_data.futures_analysis import analyze_futures_context
from app.market_data.futures_history import FuturesHistory, FuturesSeriesPoint


def point(minutes: int, value: str) -> FuturesSeriesPoint:
    return FuturesSeriesPoint(
        timestamp=datetime(2026, 11, 21, 12, minutes, tzinfo=UTC),
        value=Decimal(value),
    )


def history(funding: str, oi_previous: str, oi_latest: str, ratio: str) -> FuturesHistory:
    return FuturesHistory(
        provider="binance_usdm_futures",
        market_type="linear_perpetual",
        symbol="BTCUSDT",
        period="1h",
        funding_rate=[point(0, funding)],
        open_interest=[point(0, oi_previous), point(30, oi_latest)],
        long_short_ratio=[point(0, ratio)],
        observed_at=datetime(2026, 11, 21, 13, tzinfo=UTC),
    )


def candles(previous: str, latest: str) -> list[Candle]:
    return [
        Candle(
            timestamp=datetime(2026, 11, 21, 11, tzinfo=UTC),
            open=Decimal(previous), high=Decimal(previous), low=Decimal(previous),
            close=Decimal(previous), volume=Decimal("1"),
        ),
        Candle(
            timestamp=datetime(2026, 11, 21, 12, tzinfo=UTC),
            open=Decimal(latest), high=Decimal(latest), low=Decimal(latest),
            close=Decimal(latest), volume=Decimal("1"),
        ),
    ]


def test_rising_price_and_open_interest_is_descriptive_buildup():
    result = analyze_futures_context(
        history("0.0001", "100", "110", "1.1"),
        candles("100", "105"),
    )

    assert result.positioning == "long_and_short_build_up_with_rising_price"
    assert result.price_change_pct == Decimal("5")
    assert result.open_interest_change_pct == Decimal("10")
    assert result.confidence == "medium"
    assert any(signal.code == "price_up_oi_up" for signal in result.signals)


def test_falling_price_open_interest_and_negative_funding_flags_short_conditions():
    result = analyze_futures_context(
        history("-0.0012", "100", "120", "0.7"),
        candles("100", "105"),
    )

    assert result.positioning == "long_and_short_build_up_with_rising_price"
    assert result.funding_bias == "negative_crowding_risk"
    assert result.squeeze_risk == "short_squeeze_conditions"
    assert any(signal.code == "funding_high_negative" for signal in result.signals)


def test_rising_price_with_falling_open_interest_is_not_called_new_long_build_up():
    result = analyze_futures_context(
        history("0.0001", "100", "90", "1.0"),
        candles("100", "102"),
    )

    assert result.positioning == "short_covering_or_deleveraging"
    assert result.squeeze_risk == "no_strong_rule_based_condition"


def test_missing_series_reduces_confidence_and_discloses_proxy_price():
    data = history("0", "100", "100", "1")
    data = data.model_copy(update={"funding_rate": [], "long_short_ratio": []})
    result = analyze_futures_context(data, candles("100", "101"))

    assert result.confidence == "low"
    assert result.funding_bias == "unknown"
    assert any("Spot" in item for item in result.caveats)


def test_no_candle_change_or_zero_previous_open_interest_is_handled():
    data = history("0", "0", "100", "1")
    result = analyze_futures_context(data, [])

    assert result.price_change_pct is None
    assert result.open_interest_change_pct is None
    assert result.positioning == "insufficient_data"
