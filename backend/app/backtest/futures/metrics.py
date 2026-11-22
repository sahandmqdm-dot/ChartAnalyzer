from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal("0")
HUNDRED = Decimal("100")


@dataclass(frozen=True)
class BacktestMetrics:
    net_pnl: Decimal
    net_return_pct: Decimal
    max_drawdown_pct: Decimal
    trade_count: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: Decimal
    profit_factor: Decimal | None
    expectancy: Decimal


def calculate_metrics(initial_equity: Decimal, final_equity: Decimal, equity_curve: list[Decimal], trade_pnls: list[Decimal]) -> BacktestMetrics:
    if not initial_equity.is_finite() or initial_equity <= ZERO:
        raise ValueError("initial_equity must be finite and positive")
    if not final_equity.is_finite() or any(not value.is_finite() for value in equity_curve + trade_pnls):
        raise ValueError("equity and trade values must be finite")
    net_pnl = final_equity - initial_equity
    net_return = net_pnl / initial_equity * HUNDRED
    peak = initial_equity
    max_dd = ZERO
    for value in equity_curve:
        if value > peak:
            peak = value
        if peak > ZERO:
            drawdown = (peak - value) / peak * HUNDRED
            max_dd = max(max_dd, drawdown)
    wins = [pnl for pnl in trade_pnls if pnl > ZERO]
    losses = [pnl for pnl in trade_pnls if pnl < ZERO]
    gross_profit = sum(wins, ZERO)
    gross_loss = abs(sum(losses, ZERO))
    profit_factor = gross_profit / gross_loss if gross_loss > ZERO else None
    count = len(trade_pnls)
    win_rate = Decimal(len(wins)) / count * HUNDRED if count else ZERO
    expectancy = sum(trade_pnls, ZERO) / count if count else ZERO
    return BacktestMetrics(
        net_pnl=net_pnl, net_return_pct=net_return, max_drawdown_pct=max_dd,
        trade_count=count, winning_trades=len(wins), losing_trades=len(losses),
        win_rate_pct=win_rate, profit_factor=profit_factor, expectancy=expectancy,
    )
