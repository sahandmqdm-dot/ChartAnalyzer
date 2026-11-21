from collections.abc import Sequence
from dataclasses import asdict, dataclass
from decimal import Decimal

from app.market_data.candle import Candle

ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)


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
class EquityPoint:
    timestamp: str
    equity: Decimal
    drawdown_pct: Decimal


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
    profit_factor: Decimal | None
    expectancy: Decimal
    average_win: Decimal
    average_loss: Decimal
    exposure_pct: Decimal
    trades: tuple[BacktestTrade, ...]
    equity_curve: tuple[EquityPoint, ...]

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
            "expectancy": str(self.expectancy),
            "average_win": str(self.average_win),
            "average_loss": str(self.average_loss),
            "exposure_pct": str(self.exposure_pct),
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
            "equity_curve": [
                {
                    "timestamp": point.timestamp,
                    "equity": str(point.equity),
                    "drawdown_pct": str(point.drawdown_pct),
                }
                for point in self.equity_curve
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
    at the following candle open. Equity is marked to each candle close using an
    estimated liquidation fee; a final open position is liquidated with slippage.
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
    equity_curve: list[EquityPoint] = []
    entry_price: Decimal | None = None
    entry_time: str | None = None
    quantity = ZERO
    exposed_candles = 0

    for index, candle in enumerate(candles):
        if index >= slow_period + 1:
            signal_index = index - 1
            fast_now = _sma(closes, signal_index, fast_period)
            slow_now = _sma(closes, signal_index, slow_period)
            fast_before = _sma(closes, signal_index - 1, fast_period)
            slow_before = _sma(closes, signal_index - 1, slow_period)
            bullish_cross = fast_before <= slow_before and fast_now > slow_now
            bearish_cross = fast_before >= slow_before and fast_now < slow_now

            if entry_price is None and bullish_cross:
                entry_price = candle.open * (ONE + slippage_rate)
                entry_time = candle.timestamp.isoformat()
                quantity = equity / (entry_price * (ONE + fee_rate))
            elif entry_price is not None and bearish_cross:
                exit_price = candle.open * (ONE - slippage_rate)
                net_value = quantity * exit_price * (ONE - fee_rate)
                pnl = net_value - equity
                trade_return = (net_value / equity - ONE) * HUNDRED
                trades.append(
                    BacktestTrade(
                        entry_time=entry_time or "",
                        exit_time=candle.timestamp.isoformat(),
                        entry_price=entry_price,
                        exit_price=exit_price,
                        quantity=quantity,
                        pnl=pnl,
                        return_pct=trade_return,
                    )
                )
                equity = net_value
                entry_price = None
                entry_time = None
                quantity = ZERO

        if entry_price is not None:
            marked_equity = quantity * candle.close * (ONE - fee_rate)
            exposed_candles += 1
        else:
            marked_equity = equity

        peak_equity = max(peak_equity, marked_equity)
        drawdown = (
            (peak_equity - marked_equity) / peak_equity * HUNDRED
            if peak_equity > ZERO
            else ZERO
        )
        max_drawdown = max(max_drawdown, drawdown)
        point_time = (candle.close_time or candle.timestamp).isoformat()
        equity_curve.append(
            EquityPoint(
                timestamp=point_time,
                equity=marked_equity,
                drawdown_pct=drawdown,
            )
        )

    if entry_price is not None:
        final_candle = candles[-1]
        exit_price = final_candle.close * (ONE - slippage_rate)
        net_value = quantity * exit_price * (ONE - fee_rate)
        pnl = net_value - equity
        trade_return = (net_value / equity - ONE) * HUNDRED
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
        equity = net_value
        peak_equity = max(peak_equity, equity)
        drawdown = (
            (peak_equity - equity) / peak_equity * HUNDRED
            if peak_equity > ZERO
            else ZERO
        )
        max_drawdown = max(max_drawdown, drawdown)
        equity_curve[-1] = EquityPoint(
            timestamp=equity_curve[-1].timestamp,
            equity=equity,
            drawdown_pct=drawdown,
        )

    wins = [trade.pnl for trade in trades if trade.pnl > ZERO]
    losses = [trade.pnl for trade in trades if trade.pnl < ZERO]
    gross_profit = sum(wins, ZERO)
    gross_loss = abs(sum(losses, ZERO))
    profit_factor = gross_profit / gross_loss if gross_loss > ZERO else None
    trade_count = len(trades)
    win_rate = Decimal(len(wins)) / Decimal(trade_count) * HUNDRED if trade_count else ZERO
    expectancy = sum((trade.pnl for trade in trades), ZERO) / Decimal(trade_count) if trade_count else ZERO
    average_win = gross_profit / Decimal(len(wins)) if wins else ZERO
    average_loss = sum(losses, ZERO) / Decimal(len(losses)) if losses else ZERO
    net_return = (equity / initial_equity - ONE) * HUNDRED
    exposure = Decimal(exposed_candles) / Decimal(len(candles)) * HUNDRED

    return BacktestResult(
        initial_equity=initial_equity,
        final_equity=equity,
        net_return_pct=net_return,
        max_drawdown_pct=max_drawdown,
        trade_count=trade_count,
        winning_trades=len(wins),
        losing_trades=len(losses),
        win_rate_pct=win_rate,
        profit_factor=profit_factor,
        expectancy=expectancy,
        average_win=average_win,
        average_loss=average_loss,
        exposure_pct=exposure,
        trades=tuple(trades),
        equity_curve=tuple(equity_curve),
    )
