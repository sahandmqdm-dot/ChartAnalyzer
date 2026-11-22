from dataclasses import dataclass
from decimal import Decimal

from app.backtest.futures.margin import liquidation_price
from app.backtest.futures.position import Position


@dataclass(frozen=True)
class LiquidationResult:
    liquidated: bool
    liquidation_price: Decimal | None
    estimated_fee: Decimal
    reason: str


def check_candle_liquidation(position: Position, low: Decimal, high: Decimal, maintenance_rate: Decimal, fee_rate: Decimal = Decimal(0)) -> LiquidationResult:
    """Conservative OHLC touch check; fill at estimated liquidation price, not candle close."""
    if low <= 0 or high < low:
        raise ValueError("invalid candle range")
    liq = liquidation_price(position, maintenance_rate, fee_rate)
    touched = low <= liq if position.side == "long" else high >= liq
    if not touched:
        return LiquidationResult(False, None, Decimal(0), "liquidation level not touched")
    fee = position.quantity * liq * fee_rate
    return LiquidationResult(True, liq, fee, "mark-price liquidation level approximated by OHLC range touch")
