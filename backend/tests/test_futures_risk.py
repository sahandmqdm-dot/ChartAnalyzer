from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.market_data.futures_risk import (
    IsolatedRiskEstimateRequest,
    RiskTier,
    estimate_isolated_liquidation,
)


def tiers():
    return [
        RiskTier(
            tier=1, notional_floor=Decimal(0), notional_cap=Decimal(1000),
            maintenance_margin_rate=Decimal("0.02"),
            maintenance_margin_deduction=Decimal(0), max_leverage=Decimal(50),
        ),
        RiskTier(
            tier=2, notional_floor=Decimal(1000), notional_cap=Decimal(2000),
            maintenance_margin_rate=Decimal("0.05"),
            maintenance_margin_deduction=Decimal(30), max_leverage=Decimal(20),
        ),
    ]


def request(side="long", **updates):
    values = {
        "symbol": "BTCUSDT", "side": side,
        "entry_price": Decimal(100), "quantity": Decimal(9),
        "leverage": Decimal(10), "extra_margin": Decimal(0),
        "estimated_close_fee_rate": Decimal("0.0005"),
        "risk_tiers": tiers(),
    }
    values.update(updates)
    return IsolatedRiskEstimateRequest(**values)


def test_long_liquidation_estimate_uses_tier_deduction_and_fee():
    result = estimate_isolated_liquidation(request())
    assert result.side == "long"
    assert result.entry_notional == Decimal(900)
    assert result.liquidation_price_estimate > 0
    assert result.liquidation_price_estimate < Decimal(100)
    assert result.maintenance_margin_rate == Decimal("0.02")
    assert result.estimated_close_fee > 0
    assert result.distance_to_liquidation_percent > 0
    assert result.margin_mode == "isolated"


def test_short_liquidation_estimate_is_above_entry():
    result = estimate_isolated_liquidation(request("short"))
    assert result.liquidation_price_estimate > Decimal(100)
    assert result.distance_to_liquidation_percent > 0


def test_extra_margin_moves_long_liquidation_further_away():
    baseline = estimate_isolated_liquidation(request())
    safer = estimate_isolated_liquidation(
        request(extra_margin=Decimal(100))
    )
    assert safer.liquidation_price_estimate < baseline.liquidation_price_estimate


def test_rejects_leverage_above_entry_tier_maximum():
    with pytest.raises(ValueError, match="maximum"):
        estimate_isolated_liquidation(request(leverage=Decimal(60)))


def test_rejects_entry_notional_outside_tier_table():
    with pytest.raises(ValueError, match="outside"):
        estimate_isolated_liquidation(
            request(entry_price=Decimal(200), quantity=Decimal(10))
        )


def test_rejects_non_contiguous_tier_table():
    with pytest.raises(ValidationError, match="contiguous"):
        request(risk_tiers=[
            tiers()[0],
            RiskTier(
                tier=2, notional_floor=Decimal(1200), notional_cap=None,
                maintenance_margin_rate=Decimal("0.05"),
                maintenance_margin_deduction=Decimal(30),
            ),
        ])


def test_rejects_non_positive_fee_rate_boundaries():
    with pytest.raises(ValidationError):
        request(estimated_close_fee_rate=Decimal(1))



def test_bankruptcy_price_is_separate_from_liquidation_price():
    result = estimate_isolated_liquidation(request())
    assert result.bankruptcy_price_estimate == Decimal(90)
    assert result.liquidation_price_estimate > result.bankruptcy_price_estimate


def test_short_bankruptcy_price_is_above_entry():
    result = estimate_isolated_liquidation(request("short"))
    assert result.bankruptcy_price_estimate == Decimal(110)


def test_tier_selection_recalculates_for_liquidation_notional():
    result = estimate_isolated_liquidation(
        request(entry_price=Decimal(150), quantity=Decimal(10), leverage=Decimal(10))
    )
    assert result.liquidation_tier == 2
    assert result.maintenance_margin_rate == Decimal("0.05")


def test_rejects_maintenance_deduction_above_margin_at_tier_floor():
    with pytest.raises(ValidationError, match="deduction"):
        request(risk_tiers=[
            RiskTier(
                tier=1, notional_floor=Decimal(0), notional_cap=Decimal(1000),
                maintenance_margin_rate=Decimal("0.02"),
                maintenance_margin_deduction=Decimal(1),
            ),
            RiskTier(
                tier=2, notional_floor=Decimal(1000), notional_cap=None,
                maintenance_margin_rate=Decimal("0.05"),
                maintenance_margin_deduction=Decimal(30),
            ),
        ])


def test_mark_price_distance_reports_amount_and_percent():
    result = estimate_isolated_liquidation(
        request(mark_price=Decimal(95))
    )
    assert result.distance_to_liquidation_from_mark is not None
    assert result.distance_to_liquidation_from_mark > 0
    assert result.distance_to_liquidation_from_mark_percent == (
        result.distance_to_liquidation_from_mark / Decimal(95) * Decimal(100)
    )


def test_mark_price_distance_can_be_negative_after_trigger():
    result = estimate_isolated_liquidation(
        request(mark_price=Decimal(90))
    )
    assert result.distance_to_liquidation_from_mark < 0


def test_rejects_leverage_below_one():
    with pytest.raises(ValidationError):
        request(leverage=Decimal("0.5"))
