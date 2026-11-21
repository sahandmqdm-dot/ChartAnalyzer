from datetime import UTC, datetime
from decimal import Decimal

from app.market_data.market_structure import MarketStructureAnalysis
from app.market_data.multi_timeframe import build_multi_timeframe_confluence


def _analysis(period: str, trend: str) -> MarketStructureAnalysis:
    return MarketStructureAnalysis(
        symbol="BTCUSDT", period=period, provider="binance_usdm_futures",
        trend=trend, latest_close=Decimal(100), swings=[], events=[],
        fair_value_gaps=[], order_blocks=[], liquidity_levels=[], closed_candles=100,
        pivot_window=3, observed_at=datetime(2026, 11, 21, tzinfo=UTC), caveats=[],
    )


def test_multi_timeframe_confluence_reports_full_alignment():
    result = build_multi_timeframe_confluence(
        symbol="BTCUSDT", provider="binance_usdm_futures",
        analyses=[_analysis(period, "bullish") for period in ("5m", "15m", "1h", "4h")],
        observed_at=datetime(2026, 11, 21, tzinfo=UTC),
    )
    assert result.alignment == "aligned"
    assert result.dominant_bias == "bullish"
    assert result.bullish_timeframes == 4
    assert result.neutral_timeframes == 0


def test_multi_timeframe_confluence_reports_partial_alignment():
    result = build_multi_timeframe_confluence(
        symbol="BTCUSDT", provider="binance_usdm_futures",
        analyses=[
            _analysis("5m", "bullish"), _analysis("15m", "bullish"),
            _analysis("1h", "range"), _analysis("4h", "range"),
        ],
        observed_at=datetime(2026, 11, 21, tzinfo=UTC),
    )
    assert result.alignment == "partial_alignment"
    assert result.dominant_bias == "bullish"
    assert result.neutral_timeframes == 2


def test_multi_timeframe_confluence_reports_conflict_without_forcing_direction():
    result = build_multi_timeframe_confluence(
        symbol="BTCUSDT", provider="bybit_linear_futures",
        analyses=[
            _analysis("5m", "bullish"), _analysis("15m", "bearish"),
            _analysis("1h", "bullish"), _analysis("4h", "bearish"),
        ],
        observed_at=datetime(2026, 11, 21, tzinfo=UTC),
    )
    assert result.alignment == "conflicted"
    assert result.dominant_bias == "neutral"
    assert result.bullish_timeframes == result.bearish_timeframes == 2


def test_multi_timeframe_confluence_requires_two_directional_timeframes():
    result = build_multi_timeframe_confluence(
        symbol="BTCUSDT", provider="binance_usdm_futures",
        analyses=[_analysis(period, "range") for period in ("5m", "15m", "1h", "4h")],
        observed_at=datetime(2026, 11, 21, tzinfo=UTC),
    )
    assert result.alignment == "insufficient_directional_data"
    assert result.dominant_bias == "neutral"
