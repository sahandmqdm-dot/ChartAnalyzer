import pytest

from tiered_margin_engine import (
    calculate_tiered_isolated_margin,
    select_margin_tier,
    validate_margin_tiers,
)


@pytest.fixture
def tiers():
    return [
        {"notional_cap": 10_000, "maintenance_margin_rate": 0.005,
         "maintenance_amount": 0, "max_leverage": 50},
        {"notional_cap": 50_000, "maintenance_margin_rate": 0.01,
         "maintenance_amount": 50, "max_leverage": 20},
        {"notional_cap": 200_000, "maintenance_margin_rate": 0.025,
         "maintenance_amount": 800, "max_leverage": 10},
    ]


def test_selects_correct_notional_bracket(tiers):
    assert select_margin_tier(8_000, tiers)["max_leverage"] == 50
    assert select_margin_tier(20_000, tiers)["maintenance_margin_rate"] == .01
    with pytest.raises(ValueError, match="exceeds the highest"):
        select_margin_tier(200_001, tiers)


def test_long_and_short_liquidation_are_directional(tiers):
    long = calculate_tiered_isolated_margin(
        side="long", entry_price=100, quantity=10, leverage=10,
        maintenance_margin_tiers=tiers,
    )
    short = calculate_tiered_isolated_margin(
        side="short", entry_price=100, quantity=10, leverage=10,
        maintenance_margin_tiers=tiers,
    )
    assert long["estimated_liquidation_price"] < 100
    assert short["estimated_liquidation_price"] > 100
    assert long["actionable"] is False


def test_uses_tier_maintenance_deduction(tiers):
    out = calculate_tiered_isolated_margin(
        side="long", entry_price=2_000, quantity=10, leverage=5,
        maintenance_margin_tiers=tiers,
    )
    assert out["entry_maintenance_tier"]["maintenance_amount"] == 50
    assert out["maintenance_margin_at_entry"] == pytest.approx(150)


def test_additional_margin_moves_liquidation_away(tiers):
    base = calculate_tiered_isolated_margin(
        side="long", entry_price=100, quantity=10, leverage=10,
        maintenance_margin_tiers=tiers,
    )
    extra = calculate_tiered_isolated_margin(
        side="long", entry_price=100, quantity=10, leverage=10,
        additional_margin=20, maintenance_margin_tiers=tiers,
    )
    assert extra["estimated_liquidation_price"] < base["estimated_liquidation_price"]


def test_mark_price_uses_current_tier_and_reports_health(tiers):
    out = calculate_tiered_isolated_margin(
        side="long", entry_price=100, quantity=10, leverage=10,
        mark_price=99, maintenance_margin_tiers=tiers,
    )
    assert out["current_maintenance_tier"]["max_leverage"] == 50
    assert out["unrealized_pnl"] == pytest.approx(-10)
    assert out["margin_health"] in {"healthy", "warning", "critical", "liquidation_threshold_reached"}


def test_rejects_leverage_above_tier_limit(tiers):
    with pytest.raises(ValueError, match="exceeds selected tier maximum"):
        calculate_tiered_isolated_margin(
            side="long", entry_price=2_000, quantity=10, leverage=11,
            maintenance_margin_tiers=tiers,
        )


def test_rejects_incomplete_or_non_monotonic_tiers(tiers):
    with pytest.raises(ValueError, match="missing"):
        validate_margin_tiers([{"notional_cap": 100}])
    bad = [dict(tiers[0]), dict(tiers[1], notional_cap=9_000)]
    with pytest.raises(ValueError, match="strictly increasing"):
        validate_margin_tiers(bad)
    bad_rate = [dict(tiers[0]), dict(tiers[1], maintenance_margin_rate=.001)]
    with pytest.raises(ValueError, match="non-decreasing"):
        validate_margin_tiers(bad_rate)


@pytest.mark.parametrize("kwargs", [
    {"side": "buy", "entry_price": 100, "quantity": 1, "leverage": 2},
    {"side": "long", "entry_price": 0, "quantity": 1, "leverage": 2},
    {"side": "long", "entry_price": 100, "quantity": 0, "leverage": 2},
    {"side": "long", "entry_price": 100, "quantity": 1, "leverage": 0},
])
def test_rejects_unsafe_inputs(tiers, kwargs):
    with pytest.raises(ValueError):
        calculate_tiered_isolated_margin(
            **kwargs, maintenance_margin_tiers=tiers
        )


def test_mark_price_must_be_positive(tiers):
    with pytest.raises(ValueError, match="mark_price"):
        calculate_tiered_isolated_margin(
            side="long", entry_price=100, quantity=1, leverage=2,
            mark_price=0, maintenance_margin_tiers=tiers,
        )
