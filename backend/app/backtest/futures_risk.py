"""Tiered maintenance-margin and liquidation-distance helpers.

Tier selection uses entry notional as a conservative, deterministic proxy.
Real exchanges may select tiers using mark price, position size, and risk-limit
brackets that change during the position lifetime.
"""
from dataclasses import dataclass
from decimal import Decimal

from app.backtest.futures_position import IsolatedPosition

ZERO = Decimal("0")
ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class MaintenanceMarginTier:
    """A notional bracket with maintenance rate and fixed deduction."""

    min_notional: Decimal
    max_notional: Decimal | None
    maintenance_margin_rate: Decimal
    maintenance_amount: Decimal = ZERO

    def __post_init__(self) -> None:
        values = (self.min_notional, self.maintenance_margin_rate, self.maintenance_amount)
        if any(not isinstance(v, Decimal) or not v.is_finite() for v in values):
            raise ValueError("tier values must be finite Decimals")
        if self.max_notional is not None and (
            not isinstance(self.max_notional, Decimal) or not self.max_notional.is_finite()
        ):
            raise ValueError("max_notional must be a finite Decimal or None")
        if self.min_notional < ZERO or self.maintenance_amount < ZERO:
            raise ValueError("tier bounds and maintenance amount must be non-negative")
        if not ZERO <= self.maintenance_margin_rate < ONE:
            raise ValueError("maintenance_margin_rate must be in [0, 1)")
        if self.max_notional is not None and self.max_notional <= self.min_notional:
            raise ValueError("max_notional must be greater than min_notional")


def select_maintenance_tier(
    notional: Decimal, tiers: tuple[MaintenanceMarginTier, ...] | list[MaintenanceMarginTier]
) -> MaintenanceMarginTier:
    if not isinstance(notional, Decimal) or not notional.is_finite() or notional <= ZERO:
        raise ValueError("notional must be a finite positive Decimal")
    if not tiers:
        raise ValueError("at least one maintenance tier is required")
    ordered = sorted(tiers, key=lambda tier: tier.min_notional)
    for index, tier in enumerate(ordered):
        if index and ordered[index - 1].max_notional != tier.min_notional:
            raise ValueError("maintenance tiers must be contiguous and non-overlapping")
        if not index and tier.min_notional != ZERO:
            raise ValueError("maintenance tiers must start at zero")
        if tier.max_notional is None and index != len(ordered) - 1:
            raise ValueError("only the final maintenance tier may be unbounded")
        if tier.max_notional is not None and tier.min_notional <= notional < tier.max_notional:
            return tier
        if tier.max_notional is None and notional >= tier.min_notional:
            return tier
    raise ValueError("notional is outside configured maintenance tiers")


def liquidation_distance(
    side: str, mark_price: Decimal, liquidation_price: Decimal
) -> dict[str, Decimal]:
    """Return signed distance and percentage; positive means price is away from trigger."""
    for name, value in (("mark_price", mark_price), ("liquidation_price", liquidation_price)):
        if not isinstance(value, Decimal) or not value.is_finite() or value <= ZERO:
            raise ValueError(f"{name} must be a finite positive Decimal")
    if side == "long":
        distance = mark_price - liquidation_price
    elif side == "short":
        distance = liquidation_price - mark_price
    else:
        raise ValueError("side must be 'long' or 'short'")
    return {
        "distance": distance,
        "distance_pct": distance / mark_price * Decimal("100"),
    }


def estimate_tiered_liquidation_price(
    position: IsolatedPosition,
    tiers: tuple[MaintenanceMarginTier, ...] | list[MaintenanceMarginTier],
    margin_adjustment: Decimal = ZERO,
    liquidation_fee_rate: Decimal = ZERO,
) -> tuple[Decimal, MaintenanceMarginTier]:
    """Estimate trigger using entry-notional tier and maintenance amount deduction."""
    if not isinstance(margin_adjustment, Decimal) or not margin_adjustment.is_finite():
        raise ValueError("margin_adjustment must be a finite Decimal")
    if not isinstance(liquidation_fee_rate, Decimal) or not liquidation_fee_rate.is_finite():
        raise ValueError("liquidation_fee_rate must be a finite Decimal")
    if not ZERO <= liquidation_fee_rate < ONE:
        raise ValueError("liquidation_fee_rate must be in [0, 1)")
    tier = select_maintenance_tier(position.notional, tiers)
    rate = tier.maintenance_margin_rate
    if rate + liquidation_fee_rate >= ONE:
        raise ValueError("maintenance and liquidation fee rates must sum to less than 1")
    margin = max(ZERO, position.isolated_margin + margin_adjustment)
    q, entry, deduction = position.quantity, position.entry_price, tier.maintenance_amount
    if position.side == "long":
        price = (q * entry - margin - deduction) / (q * (ONE - rate - liquidation_fee_rate))
    else:
        price = (q * entry + margin + deduction) / (q * (ONE + rate + liquidation_fee_rate))
    return max(ZERO, price), tier
