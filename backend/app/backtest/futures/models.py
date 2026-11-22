from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal


Side = Literal["long", "short"]
ZERO = Decimal("0")


def require_positive(name: str, value: Decimal) -> None:
    if not value.is_finite() or value <= ZERO:
        raise ValueError(f"{name} must be finite and positive")


def require_non_negative(name: str, value: Decimal) -> None:
    if not value.is_finite() or value < ZERO:
        raise ValueError(f"{name} must be finite and non-negative")


@dataclass(frozen=True)
class FuturesBacktestConfig:
    initial_balance: Decimal
    leverage: Decimal = Decimal("1")
    fee_rate: Decimal = Decimal("0.0005")
    slippage_rate: Decimal = Decimal("0")
    maintenance_margin_rate: Decimal = Decimal("0.005")
    funding_enabled: bool = True
    liquidation_enabled: bool = True

    def __post_init__(self) -> None:
        require_positive("initial_balance", self.initial_balance)
        require_positive("leverage", self.leverage)
        require_non_negative("fee_rate", self.fee_rate)
        require_non_negative("slippage_rate", self.slippage_rate)
        if self.fee_rate >= 1 or self.slippage_rate >= 1:
            raise ValueError("fee and slippage rates must be below 1")
        if not self.maintenance_margin_rate.is_finite() or not (ZERO < self.maintenance_margin_rate < 1):
            raise ValueError("maintenance_margin_rate must be between 0 and 1")


@dataclass(frozen=True)
class FundingEvent:
    timestamp: datetime
    symbol: str
    funding_rate: Decimal
    mark_price: Decimal

    def __post_init__(self) -> None:
        require_positive("mark_price", self.mark_price)
        if not self.funding_rate.is_finite():
            raise ValueError("funding_rate must be finite")


@dataclass(frozen=True)
class FuturesExecution:
    timestamp: datetime
    side: Side
    quantity: Decimal
    price: Decimal
    fee: Decimal
    slippage_cost: Decimal
    realized_pnl: Decimal = ZERO

    def __post_init__(self) -> None:
        require_positive("quantity", self.quantity)
        require_positive("price", self.price)
        require_non_negative("fee", self.fee)
        require_non_negative("slippage_cost", self.slippage_cost)


@dataclass(frozen=True)
class BacktestSnapshot:
    timestamp: datetime
    balance: Decimal
    equity: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    position_quantity: Decimal
    position_side: Side | None
    mark_price: Decimal
    margin_used: Decimal
    funding_paid: Decimal
    fees_paid: Decimal
