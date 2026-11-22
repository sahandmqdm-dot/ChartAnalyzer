from __future__ import annotations


from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


Side = Literal["long", "short"]
ZERO = Decimal(0)


@dataclass
class Position:
    symbol: str
    side: Side
    quantity: Decimal
    entry_price: Decimal
    leverage: Decimal
    isolated_margin: Decimal

    def __post_init__(self) -> None:
        if self.quantity <= ZERO or self.entry_price <= ZERO or self.leverage <= ZERO:
            raise ValueError("quantity, entry_price and leverage must be positive")
        if self.isolated_margin < ZERO:
            raise ValueError("isolated_margin cannot be negative")

    @property
    def notional(self) -> Decimal:
        return self.quantity * self.entry_price

    @property
    def initial_margin(self) -> Decimal:
        return self.notional / self.leverage


    def unrealized_pnl(self, mark_price: Decimal) -> Decimal:
        if mark_price <= ZERO:
            raise ValueError("mark_price must be positive")
        raw = (mark_price - self.entry_price) * self.quantity
        return raw if self.side == "long" else -raw


    def close(self, quantity: Decimal, exit_price: Decimal) -> Decimal:
        if quantity <= ZERO or quantity > self.quantity:
            raise ValueError("close quantity must be positive and no greater than position size")
        if exit_price <= ZERO:
            raise ValueError("exit price must be positive")
        pnl = (exit_price - self.entry_price) * quantity
        if self.side == "short":
            pnl = -pnl
        self.quantity -= quantity
        if self.quantity == ZERO:
            self.isolated_margin = ZERO
        else:
            self.isolated_margin *= (self.quantity / (self.quantity + quantity))
        return pnl
