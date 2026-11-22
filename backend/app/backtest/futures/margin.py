from decimal import Decimal

from app.backtest.futures.position import Position

ZERO = Decimal(0)


def initial_margin(notional: Decimal, leverage: Decimal) -> Decimal:
    if notional <= ZERO or leverage <= ZERO:
        raise ValueError("notional and leverage must be positive")
    return notional / leverage



def maintenance_margin(mark_price: Decimal, quantity: Decimal, rate: Decimal, deduction: Decimal = ZERO) -> Decimal:
    if mark_price <= ZERO or quantity <= ZERO or not (ZERO <= rate < 1) or deduction < ZERO:
        raise ValueError("invalid maintenance-margin inputs")
    return max(ZERO, mark_price * quantity * rate - deduction)



def liquidation_price(position: Position, maintenance_rate: Decimal, fee_rate: Decimal = ZERO) -> Decimal:
    """Approximate isolated linear liquidation price; tier deduction and funding are excluded."""
    if not (ZERO <= maintenance_rate < 1) or not (ZERO <= fee_rate < 1):
        raise ValueError("rates must be in [0, 1)")
    q, entry, margin = position.quantity, position.entry_price, position.isolated_margin
    one = Decimal(1)
    if position.side == "long":
        denom = q * (one - maintenance_rate - fee_rate)
        if denom <= ZERO:
            raise ValueError("invalid liquidation denominator")
        price = (q * entry - margin) / denom
    else:
        denom = q * (one + maintenance_rate + fee_rate)
        price = (margin + q * entry) / denom
    if price <= ZERO:
        raise ValueError("calculated liquidation price is non-positive")
    return price
