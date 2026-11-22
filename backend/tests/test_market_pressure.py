from decimal import Decimal
from types import SimpleNamespace

from app.market_data.futures_analysis import FuturesAnalysis
from app.market_data.market_pressure import assess_market_pressure


def sample_analysis(
    price="2",
    oi="4",
    funding="0.0012",
):
    return FuturesAnalysis(
        symbol="BTCUSDT",
        period="1h",
        price_change_pct=Decimal(price) if price is not None else None,
        open_interest_change_pct=Decimal(oi) if oi is not None else None,
        latest_funding_rate=Decimal(funding) if funding is not None else None,
        latest_long_short_ratio=None,
        positioning="long_and_short_build_up_with_rising_price",
        funding_bias="positive_crowding_risk",
        squeeze_risk="not_assessed",
        confidence="low",
        signals=[],
        caveats=[],
        observed_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
    )


def event(side, notional):
    return SimpleNamespace(
        liquidation_side=side,
        notional=Decimal(notional),
    )


def test_market_pressure_combines_price_oi_funding_and_liquidations():
    result = assess_market_pressure(
        sample_analysis(),
        [
            event("long", "100"),
            event("long", "50"),
            event("short", "10"),
            event("long", "20"),
        ],
    )
    assert result.bias == "mild_bearish_pressure"
    assert result.pressure_score == -1
    assert result.long_liquidation_notional == Decimal(170)
    assert result.short_liquidation_notional == Decimal(10)
    assert result.liquidation_imbalance_pct == Decimal(160) / Decimal(180) * 100
    assert "long_liquidations_dominate_observed_flow" in result.signals


def test_market_pressure_does_not_infer_direction_without_price_or_oi():
    result = assess_market_pressure(
        sample_analysis(price=None, oi=None, funding=None),
        [],
    )
    assert result.bias == "balanced_or_insufficient"
    assert result.pressure_score == 0
    assert result.confidence == "low"
    assert result.liquidation_imbalance_pct is None


def test_short_liquidations_can_add_bullish_pressure():
    result = assess_market_pressure(
        sample_analysis(price="2", oi="3", funding="-0.0015"),
        [event("short", "100"), event("short", "50"), event("long", "5")],
    )
    assert result.pressure_score == 3
    assert result.bias == "bullish_pressure"
