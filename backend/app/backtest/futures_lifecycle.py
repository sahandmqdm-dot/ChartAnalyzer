"""Single-position isolated futures account lifecycle (simulation-only)."""
from decimal import Decimal

from app.backtest.futures_accounting import funding_cashflow, linear_position_pnl, trading_fee
from app.backtest.futures_position import IsolatedPosition

ZERO = Decimal("0")


class FuturesAccount:
    """Wallet balance and one isolated position; all amounts are quote currency.

    This intentionally excludes exchange-specific maintenance-margin tiers and liquidation.
    """

    def __init__(self, initial_balance: Decimal):
        if not isinstance(initial_balance, Decimal) or not initial_balance.is_finite() or initial_balance <= ZERO:
            raise ValueError("initial_balance must be a finite Decimal greater than zero")
        self.wallet_balance = initial_balance
        self.position: IsolatedPosition | None = None
        self.entry_fee_paid = ZERO
        self.funding_total = ZERO

    @property
    def locked_margin(self) -> Decimal:
        return self.position.isolated_margin if self.position else ZERO

    @property
    def available_balance(self) -> Decimal:
        return self.wallet_balance - self.locked_margin

    @property
    def unrealized_pnl(self) -> Decimal:
        return ZERO

    def unrealized_at(self, mark_price: Decimal) -> Decimal:
        return self.position.unrealized_at(mark_price) if self.position else ZERO

    def equity_at(self, mark_price: Decimal) -> Decimal:
        return self.wallet_balance + self.unrealized_at(mark_price)

    def open_position(
        self,
        *,
        side: str,
        quantity: Decimal,
        entry_price: Decimal,
        leverage: Decimal,
        fee_rate: Decimal,
    ) -> IsolatedPosition:
        if self.position is not None:
            raise ValueError("account already has an open position")
        if any(not isinstance(v, Decimal) or not v.is_finite() for v in (quantity, entry_price, leverage, fee_rate)):
            raise ValueError("position inputs must be finite Decimals")
        if quantity <= ZERO or entry_price <= ZERO or leverage < Decimal("1"):
            raise ValueError("quantity and entry_price must be positive; leverage must be >= 1")
        if not ZERO <= fee_rate < Decimal("1"):
            raise ValueError("fee_rate must be in [0, 1)")
        margin = quantity * entry_price / leverage
        fee = trading_fee(quantity * entry_price, fee_rate)
        if margin + fee > self.available_balance:
            raise ValueError("insufficient available balance for margin and entry fee")
        position = IsolatedPosition(side, quantity, entry_price, leverage, margin)
        self.wallet_balance -= fee
        self.entry_fee_paid = fee
        self.funding_total = ZERO
        self.position = position
        return position

    def apply_funding(self, mark_price: Decimal, funding_rate: Decimal) -> Decimal:
        if self.position is None:
            raise ValueError("no open position to apply funding")
        cashflow = funding_cashflow(self.position.side, self.position.quantity, mark_price, funding_rate)
        self.wallet_balance += cashflow
        self.funding_total += cashflow
        return cashflow

    def close_position(self, exit_price: Decimal, fee_rate: Decimal) -> Decimal:
        if self.position is None:
            raise ValueError("no open position to close")
        if not isinstance(exit_price, Decimal) or not exit_price.is_finite() or exit_price <= ZERO:
            raise ValueError("exit_price must be a finite Decimal greater than zero")
        gross = linear_position_pnl(self.position.side, self.position.quantity, self.position.entry_price, exit_price)
        exit_fee = trading_fee(self.position.quantity * exit_price, fee_rate)
        realized = gross - exit_fee + self.funding_total
        self.wallet_balance += gross - exit_fee
        self.position = None
        self.entry_fee_paid = ZERO
        self.funding_total = ZERO
        return realized
