from dataclasses import asdict, dataclass
from decimal import Decimal
from collections.abc import Sequence

from app.market_data.candle import Candle

ZERO = Decimal(0)
ONE = Decimal(1)


@dataclass(frozen=True)
class BacktestTrade:
    entry_time: str
    exit_time: str
    entry_price: Decimal
    exit_price: Decimal
    quantity: Decimal
    pnl: Decimal
    return_pct: Decimal


@dataclass(frozen=True)
class BacktestResult:
    initial_equity: Decimal
    final_equity: Decimal
    net_return_pct: Decimal
    max_drawdown_pct: Decimal
    trade_count: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: Decimal
    trades: tuple[BacktestTrade, ...]

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
            "trades": [
                {
                    **asdict(trade),
                    "entry_price": str(trade.entry_price),
                    "exit_price": str(trade.exit_price),
                    "quantity": str(trade.quantity),
                    "pnl": str(trade.pnl),
                    "return_pct": str(trade.return_pct),
                }
                for trade in self.trades
            ],
        }


def _sma(values: Sequence[Decimal], end: int, period: int) -> Decimal:
    window = values[end - period + 1 : end + 1]
    return sum(window, ZERO) / Decimal(period)


def run_sma_crossover(
    candles: Sequence[Candle],
    *,
    fast_period: int = 10,
    slow_period: int = 30,
    initial_equity: Decimal = Decimal(10000),
    fee_rate: Decimal = Decimal("0.001"),
    slippage_rate: Decimal = Decimal("0.0005"),
) -> BacktestResult:
    """Run a long-only SMA crossover with next-bar-open execution.

    Signals use completed candle closes only. Entries and signal-driven exits execute
    at the following candle open. Any remaining position is liquidated at the final
    candle close. Fees and adverse slippage are charged on both sides.
    """
    if fast_period < 1 or slow_period <= fast_period:
        raise ValueError("periods must satisfy 1 <= fast_period < slow_period")
    if initial_equity <= ZERO:
        raise ValueError("initial_equity must be greater than zero")
    if not ZERO <= fee_rate < ONE or not ZERO <= slippage_rate < ONE:
        raise ValueError("fee_rate and slippage_rate must be in [0, 1)")
    if len(candles) < slow_period + 2:
        raise ValueError("not enough candles for the selected periods")
    if any(not candle.is_closed for candle in candles):
        raise ValueError("backtesting accepts closed candles only")
    if any(
        candles[index].timestamp <= candles[index - 1].timestamp
        for index in range(1, len(candles))
    ):
        raise ValueError("candles must be strictly increasing by timestamp")

    closes = [candle.close for candle in candles]
    equity = initial_equity
    peak_equity = initial_equity
    max_drawdown = ZERO
    trades: list[BacktestTrade] = []
    entry_price: Decimal | None = None
    entry_time: str | None = None
    quantity = ZERO

    # The signal for candle i is calculated from closes through candle i-1.
    for execution_index in range(slow_period + 1, len(candles)):
        signal_index = execution_index - 1
        fast_now = _sma(closes, signal_index, fast_period)
        slow_now = _sma(closes, signal_index, slow_period)
        fast_before = _sma(closes, signal_index - 1, fast_period)
        slow_before = _sma(closes, signal_index - 1, slow_period)
        bullish_cross = fast_before <= slow_before and fast_now > slow_now
        bearish_cross = fast_before >= slow_before and fast_now < slow_now
        execution_candle = candles[execution_index]

        if entry_price is None and bullish_cross:
            entry_price = execution_candle.open * (ONE + slippage_rate)
            entry_time = execution_candle.timestamp.isoformat()
            quantity = equity / (entry_price * (ONE + fee_rate))
        elif entry_price is not None and bearish_cross:
            exit_price = execution_candle.open * (ONE - slippage_rate)
            gross_value = quantity * exit_price
            net_value = gross_value * (ONE - fee_rate)
            pnl = net_value - equity
            trade_return = (net_value / equity - ONE) * Decimal(100)
            equity = net_value
            trades.append(
                BacktestTrade(
                    entry_time=entry_time or "",
                    exit_time=execution_candle.timestamp.isoformat(),
                    entry_price=entry_price,
                    exit_price=exit_price,
                    quantity=quantity,
                    pnl=pnl,
                    return_pct=trade_return,
                )
            )
            entry_price = None
            entry_time = None
            quantity = ZERO
            peak_equity = max(peak_equity, equity)
            drawdown = (peak_equity - equity) / peak_equity * Decimal(100)
            max_drawdown = max(max_drawdown, drawdown)

    if entry_price is not None:
        final_candle = candles[-1]
        exit_price = final_candle.close * (ONE - slippage_rate)
        net_value = quantity * exit_price * (ONE - fee_rate)
        pnl = net_value - equity
        trade_return = (net_value / equity - ONE) * Decimal(100)
        equity = net_value
        trades.append(
            BacktestTrade(
                entry_time=entry_time or "",
                exit_time=final_candle.timestamp.isoformat(),
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=quantity,
                pnl=pnl,
                return_pct=trade_return,
            )
        )
        peak_equity = max(peak_equity, equity)
        drawdown = (peak_equity - equity) / peak_equity * Decimal(100)
        max_drawdown = max(max_drawdown, drawdown)

    wins = sum(trade.pnl > ZERO for trade in trades)
    losses = len(trades) - wins
    net_return = (equity / initial_equity - ONE) * Decimal(100)
    win_rate = Decimal(wins) / Decimal(len(trades)) * Decimal(100) if trades else ZERO
    return BacktestResult(
        initial_equity=initial_equity,
        final_equity=equity,
        net_return_pct=net_return,
        max_drawdown_pct=max_drawdown,
        trade_count=len(trades),
        winning_trades=wins,
        losing_trades=losses,
        win_rate_pct=win_rate,
        trades=tuple(trades),
    )
