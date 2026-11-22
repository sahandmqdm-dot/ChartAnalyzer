"""Pure accounting primitives for linear USDT-margined futures.

These functions intentionally do not model exchange rules, liquidation, or order execution.
"""
from decimal import Decimal
from typing import Literal

ZERO = Decimal("0")
ONE = Decimal("1")
Side = Literal["long", "short"]


def _finite(value: Decimal, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    return value


def _positive(value: Decimal, name: str) -> Decimal:
    _finite(value, name)
    if value <= ZERO:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _rate(value: Decimal, name: str, *, allow_negative: bool = False) -> Decimal:
    _finite(value, name)
    if value >= ONE or (not allow_negative and value < ZERO):
        raise ValueError(f"{name} must be {'in [-inf, 1)' if allow_negative else 'in [0, 1)'}")
    return value


def linear_position_pnl(side: Side, quantity: Decimal, entry_price: Decimal, exit_price: Decimal) -> Decimal:
    """Gross realized PnL for a linear contract; quantity and prices must be positive."""
    if side not in ("long", "short"):
        raise ValueError("side must be 'long' or 'short'")
    quantity = _positive(quantity, "quantity")
    entry_price = _positive(entry_price, "entry_price")
    exit_price = _positive(exit_price, "exit_price")
    price_delta = exit_price - entry_price
    return quantity * price_delta if side == "long" else quantity * -price_delta


def trading_fee(notional: Decimal, fee_rate: Decimal) -> Decimal:
    """Fee charged against positive quote-currency notional."""
    notional = _positive(notional, "notional")
    fee_rate = _rate(fee_rate, "fee_rate")
    return notional * fee_rate


def funding_cashflow(side: Side, quantity: Decimal, mark_price: Decimal, funding_rate: Decimal) -> Decimal:
    """Cashflow to the position: positive means received, negative means paid.

    Positive funding rates mean longs pay shorts; negative rates reverse that.
    """
    if side not in ("long", "short"):
        raise ValueError("side must be 'long' or 'short'")
    quantity = _positive(quantity, "quantity")
    mark_price = _positive(mark_price, "mark_price")
    _finite(funding_rate, "funding_rate")
    notional = quantity * mark_price
    return -notional * funding_rate if side == "long" else notional * funding_rate


def position_notional(quantity: Decimal, price: Decimal) -> Decimal:
    """Quote-currency notional for a linear contract."""
    return _positive(quantity, "quantity") * _positive(price, "price")
