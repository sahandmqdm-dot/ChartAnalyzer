"""Closed-candle Mark Price liquidation simulation for isolated linear positions.

This is a conservative OHLC approximation, not an exchange matching/liquidation engine.
It uses the simplified threshold from futures_liquidation and never reads candles after
the first open candle. Intrabar event ordering cannot be reconstructed from OHLC data.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.backtest.futures_accounting import linear_position_pnl, trading_fee
from app.backtest.futures_liquidation import estimate_bankruptcy_price, estimate_liquidation_price
from app.backtest.futures_position import IsolatedPosition
from app.market_data.mark_price import MarkPriceCandle

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class LiquidationEvent:
    liquidated: bool
    timestamp: datetime | None
    price: Decimal | None
    liquidation_threshold: Decimal
    bankruptcy_price: Decimal
    uncovered_gap_loss: Decimal
    gross_pnl: Decimal
    liquidation_fee: Decimal
    remaining_isolated_equity: Decimal
    processed_candles: int


def _validate_candle(candle: MarkPriceCandle) -> None:
    if candle.timestamp.tzinfo is None or candle.timestamp.utcoffset() is None:
        raise ValueError("candle timestamp must be timezone-aware")
    if candle.close_time.tzinfo is None or candle.close_time.utcoffset() is None:
        raise ValueError("candle close_time must be timezone-aware")
    if candle.close_time <= candle.timestamp:
        raise ValueError("candle close_time must be after timestamp")
    values = (candle.open, candle.high, candle.low, candle.close)
    if any(not value.is_finite() or value <= ZERO for value in values):
        raise ValueError("mark OHLC values must be finite and positive")
    if candle.high < max(candle.open, candle.close, candle.low):
        raise ValueError("invalid mark candle high")
    if candle.low > min(candle.open, candle.close, candle.high):
        raise ValueError("invalid mark candle low")


def simulate_mark_price_liquidation(
    position: IsolatedPosition,
    candles: list[MarkPriceCandle] | tuple[MarkPriceCandle, ...],
    maintenance_margin_rate: Decimal,
    liquidation_fee_rate: Decimal = ZERO,
    margin_adjustment: Decimal = ZERO,
) -> LiquidationEvent:
    """Return the first liquidation event in chronological, closed Mark Price candles.

    If a candle opens beyond the threshold, the simulated fill is its open (gap risk).
    Otherwise, a threshold touch fills at the threshold. The simulation stops at the
    first unclosed candle, preventing use of unfinished data or later candles.
    """
    if not isinstance(liquidation_fee_rate, Decimal) or not liquidation_fee_rate.is_finite():
        raise ValueError("liquidation_fee_rate must be a finite Decimal")
    if not ZERO <= liquidation_fee_rate < Decimal("1"):
        raise ValueError("liquidation_fee_rate must be in [0, 1)")
    if not isinstance(margin_adjustment, Decimal) or not margin_adjustment.is_finite():
        raise ValueError("margin_adjustment must be a finite Decimal")
    threshold = estimate_liquidation_price(
        position, maintenance_margin_rate, margin_adjustment, liquidation_fee_rate
    )
    bankruptcy = estimate_bankruptcy_price(position, margin_adjustment)
    previous_timestamp: datetime | None = None
    processed = 0

    for candle in candles:
        _validate_candle(candle)
        if previous_timestamp is not None and candle.timestamp <= previous_timestamp:
            raise ValueError("mark candles must be strictly chronological")
        previous_timestamp = candle.timestamp
        if not candle.is_closed:
            break
        processed += 1

        if threshold <= ZERO:
            continue
        if position.side == "long":
            if candle.open <= threshold:
                fill_price = candle.open
            elif candle.low <= threshold:
                fill_price = threshold
            else:
                continue
        else:
            if candle.open >= threshold:
                fill_price = candle.open
            elif candle.high >= threshold:
                fill_price = threshold
            else:
                continue

        gross = linear_position_pnl(
            position.side, position.quantity, position.entry_price, fill_price
        )
        if position.side == "long":
            uncovered_gap_loss = max(ZERO, bankruptcy - fill_price) * position.quantity
        else:
            uncovered_gap_loss = max(ZERO, fill_price - bankruptcy) * position.quantity
        fee = trading_fee(position.quantity * fill_price, liquidation_fee_rate) if liquidation_fee_rate else ZERO
        remaining = max(ZERO, position.isolated_margin + margin_adjustment + gross - fee)
        return LiquidationEvent(
            liquidated=True,
            timestamp=candle.timestamp,
            price=fill_price,
            liquidation_threshold=threshold,
            bankruptcy_price=bankruptcy,
            uncovered_gap_loss=uncovered_gap_loss,
            gross_pnl=gross,
            liquidation_fee=fee,
            remaining_isolated_equity=remaining,
            processed_candles=processed,
        )

    return LiquidationEvent(
        liquidated=False,
        timestamp=None,
        price=None,
        liquidation_threshold=threshold,
        bankruptcy_price=bankruptcy,
        uncovered_gap_loss=ZERO,
        gross_pnl=ZERO,
        liquidation_fee=ZERO,
        remaining_isolated_equity=max(ZERO, position.isolated_margin + margin_adjustment),
        processed_candles=processed,
    )
