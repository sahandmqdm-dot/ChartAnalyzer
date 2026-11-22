from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.backtest.futures.accounting import equity, apply_realized_pnl
from app.backtest.futures.execution import simulate_market_execution
from app.backtest.futures.funding import funding_payment
from app.backtest.futures.liquidation import check_candle_liquidation
from app.backtest.futures.margin import initial_margin, liquidation_price
from app.backtest.futures.metrics import calculate_metrics
from app.backtest.futures.models import FundingEvent, FuturesBacktestConfig
from app.backtest.futures.position import Position

D = Decimal
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def position(side="long", margin=D("100")):
    return Position("BTCUSDT", side, D("1"), D("100"), D("10"), margin)


def test_config_rejects_invalid_rates_and_balance():
    with pytest.raises(ValueError):
        FuturesBacktestConfig(initial_balance=D("0"))
    with pytest.raises(ValueError):
        FuturesBacktestConfig(initial_balance=D("1000"), fee_rate=D("1"))


def test_initial_margin_and_unrealized_pnl():
    assert initial_margin(D("1000"), D("10")) == D("100")
    p = position()
    assert p.unrealized_pnl(D("110")) == D("10")
    assert p.unrealized_pnl(D("90")) == D("-10")
    assert position("short").unrealized_pnl(D("110")) == D("-10")


def test_execution_slippage_fee_and_direction():
    buy = simulate_market_execution("buy", D("2"), D("100"), D("0.001"), D("0.01"), NOW)
    sell = simulate_market_execution("sell", D("2"), D("100"), D("0.001"), D("0.01"), NOW)
    assert buy.price == D("101")
    assert sell.price == D("99")
    assert buy.fee == D("0.202")
    assert buy.slippage_cost == D("2")


def test_funding_long_pays_positive_rate_short_receives():
    event = FundingEvent(NOW, "BTCUSDT", D("0.001"), D("100"))
    assert funding_payment(position(), event) == D("0.1")
    assert funding_payment(position("short"), event) == D("-0.1")


def test_isolated_liquidation_side_and_touch_logic():
    long_pos = position()
    long_liq = liquidation_price(long_pos, D("0.005"))
    result = check_candle_liquidation(long_pos, low=long_liq, high=D("105"), maintenance_rate=D("0.005"))
    assert result.liquidated
    short_pos = position("short")
    short_liq = liquidation_price(short_pos, D("0.005"))
    result = check_candle_liquidation(short_pos, low=D("95"), high=short_liq, maintenance_rate=D("0.005"))
    assert result.liquidated


def test_accounting_equity_and_realized_pnl():
    p = position()
    assert equity(D("1000"), p, D("110")) == D("1010")
    assert apply_realized_pnl(D("1000"), D("25"), D("2")) == D("1023")



def test_metrics_returns_drawdown_win_rate_profit_factor_and_expectancy():
    result = calculate_metrics(
        initial_equity=D("1000"), final_equity=D("1100"),
        equity_curve=[D("1000"), D("1200"), D("1050"), D("1100")],
        trade_pnls=[D("100"), D("-50"), D("25")],
    )
    assert result.net_pnl == D("100")
    assert result.net_return_pct == D("10")
    assert result.max_drawdown_pct == D("12.5")
    assert result.trade_count == 3
    assert result.winning_trades == 2
    assert result.losing_trades == 1
    assert result.win_rate_pct == D("200") / D("3")
    assert result.profit_factor == D("2.5")
    assert result.expectancy == D("25")
