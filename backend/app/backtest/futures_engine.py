"""Long/short SMA futures backtest with isolated margin and Mark Price risk checks.

This deterministic research engine is a simplified linear-perpetual model. It requires
aligned OHLCV and Mark Price candles, applies funding events at supplied timestamps,
executes signal changes at the next candle open, and uses closed Mark Price OHLC for
liquidation detection. It is not a substitute for exchange-specific simulation.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from collections.abc import Sequence

from app.backtest.futures_lifecycle import FuturesAccount
from app.backtest.futures_mark_price import simulate_mark_price_liquidation
from app.market_data.candle import Candle
from app.market_data.mark_price import MarkPriceCandle

ZERO = Decimal("0")
ONE = Decimal("1")
HUNDRED = Decimal("100")


@dataclass(frozen=True, slots=True)
class FundingPayment:
    timestamp: datetime
    rate: Decimal


@dataclass(frozen=True, slots=True)
class FuturesTrade:
    side: str
    entry_time: str
    exit_time: str
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    net_pnl: Decimal
    exit_reason: str


@dataclass(frozen=True, slots=True)
class FuturesBacktestResult:
    initial_equity: Decimal
    final_equity: Decimal
    net_return_pct: Decimal
    max_drawdown_pct: Decimal
    trade_count: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: Decimal
    profit_factor: Decimal | None
    total_funding: Decimal
    liquidation_count: int
    trades: tuple[FuturesTrade, ...]

    def to_dict(self) -> dict:
        return {
            "initial_equity": str(self.initial_equity),
            "final_equity": str(self.final_equity),
            "net_return_pct": str(self.net_return_pct),
            "max_drawdown_pct": str(self.max_drawdown_pct),
            "trade_count": self.trade_count,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate_pct": str(self.win_rate_pct),
            "profit_factor": str(self.profit_factor) if self.profit_factor is not None else None,
            "total_funding": str(self.total_funding),
            "liquidation_count": self.liquidation_count,
            "trades": [
                {
                    "side": trade.side,
                    "entry_time": trade.entry_time,
                    "exit_time": trade.exit_time,
                    "entry_price": str(trade.entry_price),
                    "exit_price": str(trade.exit_price),
                    "quantity": str(trade.quantity),
                    "net_pnl": str(trade.net_pnl),
                    "exit_reason": trade.exit_reason,
                }
                for trade in self.trades
            ],
        }


def _sma(values: Sequence[Decimal], end: int, period: int) -> Decimal:
    return sum(values[end - period + 1 : end + 1], ZERO) / Decimal(period)


def run_futures_sma_crossover(
    candles: Sequence[Candle],
    mark_candles: Sequence[MarkPriceCandle],
    *,
    fast_period: int = 10,
    slow_period: int = 30,
    initial_equity: Decimal = Decimal("10000"),
    leverage: Decimal = Decimal("3"),
    margin_fraction: Decimal = Decimal("0.25"),
    fee_rate: Decimal = Decimal("0.0004"),
    slippage_rate: Decimal = Decimal("0.0002"),
    maintenance_margin_rate: Decimal = Decimal("0.005"),
    liquidation_fee_rate: Decimal = Decimal("0.01"),
    funding_payments: Sequence[FundingPayment] = (),
) -> FuturesBacktestResult:
    """Backtest a one-position SMA strategy; positive cross is long, negative is short."""
    if not 1 <= fast_period < slow_period:
        raise ValueError("periods must satisfy 1 <= fast_period < slow_period")
    decimal_values = (initial_equity, leverage, margin_fraction, fee_rate, slippage_rate,
                      maintenance_margin_rate, liquidation_fee_rate)
    if any(not isinstance(v, Decimal) or not v.is_finite() for v in decimal_values):
        raise ValueError("numeric parameters must be finite Decimals")
    if initial_equity <= ZERO or leverage < ONE:
        raise ValueError("initial_equity must be positive and leverage must be >= 1")
    if not ZERO < margin_fraction <= ONE:
        raise ValueError("margin_fraction must be in (0, 1]")
    for name, rate in (("fee_rate", fee_rate), ("slippage_rate", slippage_rate),
                       ("maintenance_margin_rate", maintenance_margin_rate),
                       ("liquidation_fee_rate", liquidation_fee_rate)):
        if not ZERO <= rate < ONE:
            raise ValueError(f"{name} must be in [0, 1)")
    if len(candles) < slow_period + 2 or len(candles) != len(mark_candles):
        raise ValueError("aligned OHLCV and Mark Price series with enough candles are required")
    if any(not c.is_closed for c in candles) or any(not c.is_closed for c in mark_candles):
        raise ValueError("backtesting accepts closed candles only")
    for i, (candle, mark) in enumerate(zip(candles, mark_candles)):
        if candle.timestamp != mark.timestamp:
            raise ValueError("OHLCV and Mark Price timestamps must align")
        if i and candle.timestamp <= candles[i - 1].timestamp:
            raise ValueError("candles must be strictly chronological")
    for payment in funding_payments:
        if (
            not isinstance(payment.timestamp, datetime)
            or payment.timestamp.tzinfo is None
            or payment.timestamp.utcoffset() is None
            or not isinstance(payment.rate, Decimal)
            or not payment.rate.is_finite()
        ):
            raise ValueError("funding events require timezone-aware timestamps and finite Decimal rates")
    ordered_funding = sorted(funding_payments, key=lambda item: item.timestamp)
    if any(ordered_funding[i].timestamp <= ordered_funding[i-1].timestamp
           for i in range(1, len(ordered_funding))):
        raise ValueError("funding events must have unique timestamps")

    account = FuturesAccount(initial_equity)
    trades: list[FuturesTrade] = []
    equity_peak = initial_equity
    max_drawdown = ZERO
    liquidation_count = 0
    funding_total = ZERO
    funding_index = 0
    entry_time = ""
    entry_price = ZERO
    entry_side = ""
    entry_quantity = ZERO
    entry_datetime: datetime | None = None
    closes = [c.close for c in candles]

    for index, candle in enumerate(candles):
        desired_side: str | None = None
        if index >= slow_period + 1:
            signal_index = index - 1
            fast_now = _sma(closes, signal_index, fast_period)
            slow_now = _sma(closes, signal_index, slow_period)
            fast_before = _sma(closes, signal_index - 1, fast_period)
            slow_before = _sma(closes, signal_index - 1, slow_period)
            if fast_before <= slow_before and fast_now > slow_now:
                desired_side = "long"
            elif fast_before >= slow_before and fast_now < slow_now:
                desired_side = "short"

        # Signal transitions execute at this candle's open, never at the signal close.
        if account.position is not None and desired_side is not None and desired_side != account.position.side:
            exit_price = candle.open * (ONE - slippage_rate if account.position.side == "long" else ONE + slippage_rate)
            net_pnl = account.close_position(exit_price, fee_rate)
            trades.append(FuturesTrade(entry_side, entry_time, candle.timestamp.isoformat(),
                                       entry_price, exit_price, entry_quantity, net_pnl, "signal"))
            entry_side = ""
            entry_datetime = None

        if account.position is None and desired_side is not None:
            execution = candle.open * (ONE + slippage_rate if desired_side == "long" else ONE - slippage_rate)
            budget = account.wallet_balance * margin_fraction
            quantity = budget / (execution / leverage + execution * fee_rate)
            try:
                account.open_position(side=desired_side, quantity=quantity, entry_price=execution,
                                      leverage=leverage, fee_rate=fee_rate)
            except ValueError as exc:
                if "insufficient available balance" not in str(exc):
                    raise
            else:
                entry_side, entry_time, entry_price, entry_quantity = (
                    desired_side, candle.timestamp.isoformat(), execution, quantity
                )
                entry_datetime = candle.timestamp

        # Apply funding events after entry and through this candle's close.
        while funding_index < len(ordered_funding) and ordered_funding[funding_index].timestamp <= (candle.close_time or candle.timestamp):
            payment = ordered_funding[funding_index]
            if account.position is not None and entry_datetime is not None and entry_datetime < payment.timestamp:
                cashflow = account.apply_funding(mark_candles[index].close, payment.rate)
                funding_total += cashflow
            funding_index += 1

        if account.position is not None:
            event = simulate_mark_price_liquidation(
                account.position, [mark_candles[index]], maintenance_margin_rate,
                liquidation_fee_rate,
            )
            if event.liquidated:
                net_pnl = account.settle_liquidation(event)
                trades.append(FuturesTrade(entry_side, entry_time, event.timestamp.isoformat(),
                                           entry_price, event.price, entry_quantity, net_pnl, "liquidation"))
                liquidation_count += 1
                entry_side = ""
                entry_datetime = None

        if account.position is not None and index == len(candles) - 1:
            final_price = candle.close * (ONE - slippage_rate if account.position.side == "long" else ONE + slippage_rate)
            net_pnl = account.close_position(final_price, fee_rate)
            trades.append(FuturesTrade(entry_side, entry_time, candle.close_time.isoformat() if candle.close_time else candle.timestamp.isoformat(),
                                       entry_price, final_price, entry_quantity, net_pnl, "end_of_data"))
            entry_side = ""
            entry_datetime = None

        mark = mark_candles[index].close
        equity = account.equity_at(mark)
        equity_peak = max(equity_peak, equity)
        drawdown = (equity_peak - equity) / equity_peak * HUNDRED if equity_peak > ZERO else ZERO
        max_drawdown = max(max_drawdown, drawdown)

    wins = [t.net_pnl for t in trades if t.net_pnl > ZERO]
    losses = [t.net_pnl for t in trades if t.net_pnl < ZERO]
    gross_profit, gross_loss = sum(wins, ZERO), abs(sum(losses, ZERO))
    count = len(trades)
    return FuturesBacktestResult(
        initial_equity=initial_equity,
        final_equity=account.wallet_balance,
        net_return_pct=(account.wallet_balance / initial_equity - ONE) * HUNDRED,
        max_drawdown_pct=max_drawdown,
        trade_count=count,
        winning_trades=len(wins),
        losing_trades=len(losses),
        win_rate_pct=Decimal(len(wins)) / Decimal(count) * HUNDRED if count else ZERO,
        profit_factor=gross_profit / gross_loss if gross_loss else None,
        total_funding=funding_total,
        liquidation_count=liquidation_count,
        trades=tuple(trades),
    )
