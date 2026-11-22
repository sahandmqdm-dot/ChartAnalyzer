from decimal import Decimal
from app.backtest.futures.models import FuturesExecution, Side


ZERO = Decimal("0")


def simulate_market_execution(side: Side, quantity: Decimal, reference_price: Decimal, fee_rate: Decimal, slippage_rate: Decimal, timestamp) -> FuturesExecution:
    if quantity <= ZERO or reference_price <= ZERO:
        raise ValueError("quantity and reference price must be positive")
    if not (ZERO <= fee_rate < 1) or not (ZERO <= slippage_rate < 1):
        raise ValueError("fee and slippage rates must be in [0, 1)")
    # Buy orders pay upward slippage; sell orders receive downward slippage.
    order_side = "buy" if side == "long" else "sell"
    fill = reference_price * (Decimal("1") + slippage_rate if order_side == "buy" else Decimal("1") - slippage_rate)
    notional = quantity * fill
    fee = notional * fee_rate
    slip_cost = quantity * abs(fill - reference_price)
    return FuturesExecution(timestamp=timestamp, side=side, quantity=quantity, price=fill, fee=fee, slippage_cost=slip_cost)
