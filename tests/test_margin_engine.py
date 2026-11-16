import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from margin_engine import calculate_isolated_margin


def test_long_liquidation_price_uses_isolated_collateral_and_reserve_rates():
    out = calculate_isolated_margin(
        entry_price=100, quantity=1, leverage=10, side="long",
        maintenance_margin_rate=0.005, liquidation_fee_rate=0.005,
    )
    assert out["initial_margin"] == pytest.approx(10)
    assert out["estimated_liquidation_price"] == pytest.approx(90 / 0.99)
    assert out["notional"] == pytest.approx(100)
    assert out["market_type"] == "futures"
    assert out["margin_mode"] == "isolated"


def test_short_liquidation_price_formula_is_directionally_correct():
    out = calculate_isolated_margin(
        entry_price=100, quantity=1, leverage=10, side="short",
        maintenance_margin_rate=0.005, liquidation_fee_rate=0.005,
    )
    assert out["estimated_liquidation_price"] == pytest.approx(110 / 1.01)
    assert out["estimated_liquidation_price"] > out["entry_price"]


def test_additional_margin_moves_liquidation_price_away_from_entry():
    base_long = calculate_isolated_margin(entry_price=100, quantity=1, leverage=10)
    extra_long = calculate_isolated_margin(entry_price=100, quantity=1, leverage=10, additional_margin=5)
    base_short = calculate_isolated_margin(entry_price=100, quantity=1, leverage=10, side="short")
    extra_short = calculate_isolated_margin(entry_price=100, quantity=1, leverage=10, side="short", additional_margin=5)
    assert extra_long["estimated_liquidation_price"] < base_long["estimated_liquidation_price"]
    assert extra_short["estimated_liquidation_price"] > base_short["estimated_liquidation_price"]


def test_mark_price_reports_unrealized_pnl_and_consistent_reserve_health():
    out = calculate_isolated_margin(
        entry_price=100, quantity=1, leverage=10, side="long", mark_price=95,
        maintenance_margin_rate=0.005, liquidation_fee_rate=0.005,
    )
    assert out["unrealized_pnl"] == pytest.approx(-5)
    assert out["current_equity_estimate"] == pytest.approx(5)
    assert out["liquidation_reserve_at_mark"] == pytest.approx(0.95)
    assert out["equity_after_liquidation_reserve"] == pytest.approx(4.05)
    assert out["margin_health"] == "warning"


def test_mark_price_health_escalates_to_critical_and_liquidation_threshold():
    critical = calculate_isolated_margin(
        entry_price=100, quantity=1, leverage=10, side="long", mark_price=92,
        warning_distance_pct=5, critical_distance_pct=2,
    )
    at_threshold = calculate_isolated_margin(
        entry_price=100, quantity=1, leverage=10, side="long",
        mark_price=critical["estimated_liquidation_price"],
    )
    assert critical["margin_health"] == "critical"
    assert at_threshold["margin_health"] == "liquidation_threshold_reached"
    assert at_threshold["distance_to_liquidation_pct"] == pytest.approx(0)


def test_short_mark_price_distance_is_measured_toward_upside_liquidation():
    out = calculate_isolated_margin(
        entry_price=100, quantity=1, leverage=10, side="short", mark_price=105,
    )
    assert out["distance_to_liquidation_pct"] > 0
    assert out["margin_health"] == "warning"


def test_mark_price_beyond_liquidation_threshold_is_not_reported_healthy():
    out = calculate_isolated_margin(
        entry_price=100, quantity=1, leverage=10, side="long", mark_price=89,
    )
    assert out["margin_health"] == "liquidation_threshold_reached"
    assert out["distance_to_liquidation_pct"] < 0


def test_spot_is_rejected_because_it_has_no_futures_liquidation_price():
    with pytest.raises(ValueError, match="Spot has no liquidation price"):
        calculate_isolated_margin(
            market_type="spot", entry_price=100, quantity=1,
        )


@pytest.mark.parametrize("kwargs", [
    {"entry_price": 0, "quantity": 1},
    {"entry_price": 100, "quantity": 0},
    {"entry_price": 100, "quantity": 1, "leverage": 0.5},
    {"entry_price": 100, "quantity": 1, "leverage": 126},
    {"entry_price": 100, "quantity": 1, "maintenance_margin_rate": -0.01},
    {"entry_price": 100, "quantity": 1, "liquidation_fee_rate": 0.5},
    {"entry_price": 100, "quantity": 1, "additional_margin": -1},
    {"entry_price": 100, "quantity": 1, "side": "buy"},
])
def test_rejects_invalid_trade_parameters(kwargs):
    with pytest.raises(ValueError):
        calculate_isolated_margin(**kwargs)


def test_rejects_non_finite_values_including_optional_mark_price():
    with pytest.raises(ValueError, match="entry_price must be a finite number"):
        calculate_isolated_margin(entry_price=float("nan"), quantity=1)
    with pytest.raises(ValueError, match="mark_price must be a finite number"):
        calculate_isolated_margin(entry_price=100, quantity=1, mark_price=float("nan"))


def test_maintenance_and_liquidation_fee_rates_must_leave_valid_denominator():
    with pytest.raises(ValueError, match="combined margin rates"):
        calculate_isolated_margin(
            entry_price=100, quantity=1, maintenance_margin_rate=0.3,
            liquidation_fee_rate=0.2,
        )


def test_threshold_configuration_is_validated():
    with pytest.raises(ValueError, match="risk thresholds"):
        calculate_isolated_margin(
            entry_price=100, quantity=1,
            warning_distance_pct=2, critical_distance_pct=2,
        )


def test_mark_price_scale_warning_flags_suspicious_unit_mismatch():
    from margin_engine import calculate_isolated_margin
    out = calculate_isolated_margin(
        market_type="futures", side="short", entry_price=68000, quantity=0.01,
        leverage=5, mark_price=5,
    )
    assert out["price_scale_warning"]
    assert out["mark_entry_deviation_pct"] > 99


def test_mark_price_scale_warning_is_empty_for_comparable_prices():
    from margin_engine import calculate_isolated_margin
    out = calculate_isolated_margin(
        market_type="futures", side="long", entry_price=68000, quantity=0.01,
        leverage=5, mark_price=66000,
    )
    assert out["price_scale_warning"] is None


def test_scale_warning_suppresses_definitive_margin_health():
    out = calculate_isolated_margin(
        entry_price=68000, quantity=0.01, leverage=5, side="long", mark_price=34000,
    )
    assert out["price_scale_warning"]
    assert out["margin_health"] == "unverified_inputs"
    assert out["margin_health_verified"] is False


def test_scale_warning_suppresses_all_mark_derived_risk_metrics():
    out = calculate_isolated_margin(
        entry_price=68000, quantity=0.01, leverage=5, side="long", mark_price=0.01,
    )
    assert out["margin_health"] == "unverified_inputs"
    assert out["risk_metrics_status"] == "suppressed_due_to_price_scale_mismatch"
    for key in ("unrealized_pnl", "current_equity_estimate", "maintenance_margin_at_mark",
                "liquidation_reserve_at_mark", "equity_after_liquidation_reserve",
                "margin_ratio_pct", "distance_to_liquidation_pct"):
        assert out[key] is None
