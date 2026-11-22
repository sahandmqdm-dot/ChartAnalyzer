from decimal import Decimal
from app.backtest.futures.models import FundingEvent
from app.backtest.futures.position import Position


def funding_payment(position: Position, event: FundingEvent) -> Decimal:
    """Positive means cash paid by the trader; negative means received."""
    if position.symbol.upper() != event.symbol.upper():
        raise ValueError("funding event symbol does not match position")
    notional = position.quantity * event.mark_price
    payment = notional * event.funding_rate
    return payment if position.side == "long" else -payment
