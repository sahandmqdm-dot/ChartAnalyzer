"""Typed immutable models for a single isolated linear futures position."""
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

ZERO = Decimal("0")
Side = Literal["long", "short"]


def _validate_decimal(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")


@dataclass(frozen=True, slots=True)
class IsolatedPosition:
    side: Side
    quantity: Decimal
    entry_price: Decimal
    leverage: Decimal
    isolated_margin: Decimal

    def __post_init__(self) -> None:
        if self.side not in ("long", "short"):
            raise ValueError("side must be 'long' or 'short'")
        for name in ("quantity", "entry_price", "leverage", "isolated_margin"):
            value = getattr(self, name)
            _validate_decimal(value, name)
            if value <= ZERO:
                raise ValueError(f"{name} must be greater than zero")
        if self.leverage < Decimal("1"):
            raise ValueError("leverage must be >= 1")

    @property
    def notional(self) -> Decimal:
        return self.quantity * self.entry_price

    @property
    def effective_leverage(self) -> Decimal:
        return self.notional / self.isolated_margin

    @property
    def unrealized_pnl(self) -> Decimal:
        """At entry this is zero; use unrealized_at(mark) for live marks."""
        return ZERO

    def unrealized_at(self, mark_price: Decimal) -> Decimal:
        _validate_decimal(mark_price, "mark_price")
        if mark_price <= ZERO:
            raise ValueError("mark_price must be greater than zero")
        move = mark_price - self.entry_price
        return self.quantity * move if self.side == "long" else -self.quantity * move
