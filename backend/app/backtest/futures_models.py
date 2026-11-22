"""Validated isolated-margin position model for linear futures backtesting."""
from dataclasses import dataclass
from decimal import Decimal

from app.backtest.futures_accounting import ONE, ZERO, Side, _finite, _positive, _rate, trading_fee


@dataclass(frozen=True)
class IsolatedPosition:
    side: Side
    quantity: Decimal
    entry_price: Decimal
    leverage: int
    allocated_margin: Decimal
    entry_fee: Decimal

    def __post_init__(self) -> None:
        if self.side not in ("long", "short"):
            raise ValueError("side must be 'long' or 'short'")
        _positive(self.quantity, "quantity")
        _positive(self.entry_price, "entry_price")
        _positive(self.allocated_margin, "allocated_margin")
        _finite(self.entry_fee, "entry_fee")
        if self.entry_fee < ZERO:
            raise ValueError("entry_fee must be non-negative")
        if not isinstance(self.leverage, int) or isinstance(self.leverage, bool) or not 1 <= self.leverage <= 125:
            raise ValueError("leverage must be an integer between 1 and 125")


def open_isolated_position(
    *,
    side: Side,
    margin: Decimal,
    leverage: int,
    entry_price: Decimal,
    fee_rate: Decimal = Decimal("0.0006"),
) -> IsolatedPosition:
    """Create a position from isolated margin and leverage (fees tracked separately)."""
    if side not in ("long", "short"):
        raise ValueError("side must be 'long' or 'short'")
    margin = _positive(margin, "margin")
    entry_price = _positive(entry_price, "entry_price")
    if not isinstance(leverage, int) or isinstance(leverage, bool) or not 1 <= leverage <= 125:
        raise ValueError("leverage must be an integer between 1 and 125")
    fee_rate = _rate(fee_rate, "fee_rate")
    notional = margin * Decimal(leverage)
    quantity = notional / entry_price
    entry_fee = trading_fee(notional, fee_rate)
    return IsolatedPosition(
        side=side,
        quantity=quantity,
        entry_price=entry_price,
        leverage=leverage,
        allocated_margin=margin,
        entry_fee=entry_fee,
    )
