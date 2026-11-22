"""Simplified liquidation-price estimator for isolated linear futures.

This is an educational backtest approximation, not an exchange liquidation formula:
exchange maintenance tiers, liquidation fees, risk brackets, and margin adjustments
must be supplied/implemented before using it to reproduce an exchange exactly.
"""
from decimal import Decimal

from app.backtest.futures_position import IsolatedPosition

ZERO = Decimal("0")
ONE = Decimal("1")


def estimate_liquidation_price(
    position: IsolatedPosition,
    maintenance_margin_rate: Decimal,
    margin_adjustment: Decimal = ZERO,
    liquidation_fee_rate: Decimal = ZERO,
) -> Decimal:
    if not isinstance(maintenance_margin_rate, Decimal) or not maintenance_margin_rate.is_finite():
        raise ValueError("maintenance_margin_rate must be a finite Decimal")
    if not ZERO <= maintenance_margin_rate < ONE:
        raise ValueError("maintenance_margin_rate must be in [0, 1)")
    if not isinstance(margin_adjustment, Decimal) or not margin_adjustment.is_finite():
        raise ValueError("margin_adjustment must be a finite Decimal")
    if not isinstance(liquidation_fee_rate, Decimal) or not liquidation_fee_rate.is_finite():
        raise ValueError("liquidation_fee_rate must be a finite Decimal")
    if not ZERO <= liquidation_fee_rate < ONE:
        raise ValueError("liquidation_fee_rate must be in [0, 1)")
    if maintenance_margin_rate + liquidation_fee_rate >= ONE:
        raise ValueError("maintenance_margin_rate plus liquidation_fee_rate must be below 1")
    q = position.quantity
    entry = position.entry_price
    # In this model cumulative funding is credited/debited to isolated position equity.
    margin = max(ZERO, position.isolated_margin + margin_adjustment)
    if position.side == "long":
        denominator = q * (ONE - maintenance_margin_rate - liquidation_fee_rate)
        price = (q * entry - margin) / denominator
    else:
        denominator = q * (ONE + maintenance_margin_rate + liquidation_fee_rate)
        price = (q * entry + margin) / denominator
    # A non-positive estimate means the isolated margin is large enough that this
    # simplified model has no positive liquidation threshold.

    return max(ZERO, price)


def estimate_bankruptcy_price(
    position: IsolatedPosition,
    margin_adjustment: Decimal = ZERO,
) -> Decimal:
    """Estimate zero-equity price, excluding maintenance and liquidation-fee reserves."""
    if not isinstance(margin_adjustment, Decimal) or not margin_adjustment.is_finite():
        raise ValueError("margin_adjustment must be a finite Decimal")
    effective_margin = max(ZERO, position.isolated_margin + margin_adjustment)
    margin_per_unit = effective_margin / position.quantity
    if position.side == "long":
        return max(ZERO, position.entry_price - margin_per_unit)
    return position.entry_price + margin_per_unit
