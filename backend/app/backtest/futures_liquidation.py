"""Simplified liquidation-price estimator for isolated linear futures.

This is an educational backtest approximation, not an exchange liquidation formula:
exchange maintenance tiers, liquidation fees, risk brackets, and margin adjustments
must be supplied/implemented before using it to reproduce an exchange exactly.
"""
from decimal import Decimal

from app.backtest.futures_position import IsolatedPosition

ZERO = Decimal("0")
ONE = Decimal("1")


def estimate_liquidation_price(position: IsolatedPosition, maintenance_margin_rate: Decimal) -> Decimal:
    if not isinstance(maintenance_margin_rate, Decimal) or not maintenance_margin_rate.is_finite():
        raise ValueError("maintenance_margin_rate must be a finite Decimal")
    if not ZERO <= maintenance_margin_rate < ONE:
        raise ValueError("maintenance_margin_rate must be in [0, 1)")
    q = position.quantity
    entry = position.entry_price
    margin = position.isolated_margin
    if position.side == "long":
        denominator = q * (ONE - maintenance_margin_rate)
        price = (q * entry - margin) / denominator
    else:
        denominator = q * (ONE + maintenance_margin_rate)
        price = (q * entry + margin) / denominator
    # A non-positive estimate means the isolated margin is large enough that this
    # simplified model has no positive liquidation threshold.
    return max(ZERO, price)
