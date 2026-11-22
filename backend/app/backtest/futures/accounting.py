from decimal import Decimal
from app.backtest.futures.position import Position


ZERO = Decimal("0")


def unrealized_pnl(position: Position | None, mark_price: Decimal) -> Decimal:
    return ZERO if position is None else position.unrealized_pnl(mark_price)


def equity(balance: Decimal, position: Position | None, mark_price: Decimal) -> Decimal:
    return balance + unrealized_pnl(position, mark_price)


def apply_realized_pnl(balance: Decimal, pnl: Decimal, fee: Decimal = ZERO) -> Decimal:
    if fee < ZERO:
        raise ValueError("fee cannot be negative")
    return balance + pnl - fee
