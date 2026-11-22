from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.backtest.futures_engine import FundingPayment, run_futures_sma_crossover
from app.market_data.candle import Candle
from app.market_data.mark_price import MarkPriceCandle

D = Decimal
START = datetime(2026, 1, 1, tzinfo=UTC)


def series(closes):
    candles = []
    marks = []
    for index, value in enumerate(closes):
        timestamp = START + timedelta(hours=index)
        price = D(str(value))
        candles.append(Candle(
            timestamp=timestamp,
            close_time=timestamp + timedelta(hours=1),
            is_closed=True,
            open=price, high=price + D("0.1"), low=price - D("0.1"),
            close=price, volume=D("100"),
        ))
        marks.append(MarkPriceCandle(
            timestamp=timestamp,
            close_time=timestamp + timedelta(hours=1),
            is_closed=True,
            open=price, high=price + D("0.2"), low=price - D("0.2"),
            close=price,
        ))
    return candles, marks


def test_futures_sma_enters_long_and_closes_at_end_of_data():
    candles, marks = series(["5", "4", "3", "6", "7", "8"])
    result = run_futures_sma_crossover(
        candles, marks, fast_period=2, slow_period=3,
        fee_rate=D("0"), slippage_rate=D("0"),
    )
    assert result.trade_count == 1
    assert result.trades[0].side == "long"
    assert result.trades[0].exit_reason == "end_of_data"
    assert result.final_equity > result.initial_equity
    assert result.to_dict()["trades"][0]["net_pnl"] == str(result.trades[0].net_pnl)
    assert result.trades[0].liquidation_trigger_price is None
    assert result.to_dict()["trades"][0]["bankruptcy_price"] is None


def test_funding_is_applied_only_while_position_is_open():
    candles, marks = series(["5", "4", "3", "6", "7"])
    payment = FundingPayment(START + timedelta(hours=4, minutes=30), D("0.001"))
    baseline = run_futures_sma_crossover(
        candles, marks, fast_period=2, slow_period=3,
        fee_rate=D("0"), slippage_rate=D("0"),
    )
    result = run_futures_sma_crossover(
        candles, marks, fast_period=2, slow_period=3,
        fee_rate=D("0"), slippage_rate=D("0"),
        funding_payments=[payment],
    )
    assert result.total_funding < D("0")
    assert result.final_equity < baseline.final_equity


def test_rejects_misaligned_mark_price_series():
    candles, marks = series(["5", "4", "3", "6", "7"])
    marks[0] = marks[0].model_copy(update={"timestamp": marks[0].timestamp + timedelta(minutes=1)})
    with pytest.raises(ValueError, match="timestamps must align"):
        run_futures_sma_crossover(candles, marks, fast_period=2, slow_period=3)


def test_rejects_unclosed_market_data():
    candles, marks = series(["5", "4", "3", "6", "7"])
    candles[0] = candles[0].model_copy(update={"is_closed": False})
    with pytest.raises(ValueError, match="closed candles"):
        run_futures_sma_crossover(candles, marks, fast_period=2, slow_period=3)


def test_rejects_naive_funding_timestamp():
    candles, marks = series(["5", "4", "3", "6", "7"])
    with pytest.raises(ValueError, match="timezone-aware"):
        run_futures_sma_crossover(
            candles, marks, fast_period=2, slow_period=3,
            funding_payments=[FundingPayment(datetime(2026, 1, 1), D("0.001"))],  # noqa: DTZ001
        )



def test_futures_sma_enters_short_and_closes_at_end_of_data():
    candles, marks = series(["3", "4", "5", "6", "7", "4", "3", "2", "1"])
    result = run_futures_sma_crossover(
        candles, marks, fast_period=2, slow_period=3,
        fee_rate=D("0"), slippage_rate=D("0"),
    )
    assert result.trade_count == 1
    assert result.trades[0].side == "short"
    assert result.trades[0].exit_reason == "end_of_data"
    assert result.trades[0].net_pnl > D("0")
    assert result.final_equity > result.initial_equity


def test_negative_funding_rate_is_received_by_long_position():
    candles, marks = series(["5", "4", "3", "6", "7"])
    payment = FundingPayment(START + timedelta(hours=4, minutes=30), D("-0.001"))
    baseline = run_futures_sma_crossover(
        candles, marks, fast_period=2, slow_period=3,
        fee_rate=D("0"), slippage_rate=D("0"),
    )
    result = run_futures_sma_crossover(
        candles, marks, fast_period=2, slow_period=3,
        fee_rate=D("0"), slippage_rate=D("0"),
        funding_payments=[payment],
    )
    assert result.total_funding > D("0")
    assert result.final_equity > baseline.final_equity

def test_long_position_is_liquidated_on_mark_price_low():
    candles, marks = series(["5", "4", "3", "6", "7"])
    marks[4] = marks[4].model_copy(update={"low": D("1")})
    result = run_futures_sma_crossover(
        candles, marks, fast_period=2, slow_period=3,
        fee_rate=D("0"), slippage_rate=D("0"),
    )
    assert result.liquidation_count == 1
    assert result.trade_count == 1
    assert result.trades[0].side == "long"
    assert result.trades[0].exit_reason == "liquidation"
    assert result.final_equity < result.initial_equity
    trade = result.trades[0]
    assert trade.liquidation_trigger_price is not None
    assert trade.bankruptcy_price is not None
    assert trade.liquidation_fill_price == trade.exit_price
    assert trade.uncovered_gap_loss is not None
    serialized = result.to_dict()["trades"][0]
    assert serialized["liquidation_trigger_price"] == str(trade.liquidation_trigger_price)
    assert serialized["bankruptcy_price"] == str(trade.bankruptcy_price)
    assert serialized["liquidation_fill_price"] == str(trade.exit_price)


def test_short_position_is_liquidated_on_mark_price_high():
    candles, marks = series(["3", "4", "5", "6", "7", "4", "3", "2", "1"])
    marks[8] = marks[8].model_copy(update={"high": D("10")})
    result = run_futures_sma_crossover(
        candles, marks, fast_period=2, slow_period=3,
        fee_rate=D("0"), slippage_rate=D("0"),
    )
    assert result.liquidation_count == 1
    assert result.trade_count == 1
    assert result.trades[0].side == "short"
    assert result.trades[0].exit_reason == "liquidation"
    assert result.final_equity < result.initial_equity

def test_rejects_mixed_naive_and_aware_funding_timestamps():
    candles, marks = series(["5", "4", "3", "6", "7"])
    with pytest.raises(ValueError, match="timezone-aware"):
        run_futures_sma_crossover(
            candles, marks, fast_period=2, slow_period=3,
            funding_payments=[
                FundingPayment(START + timedelta(hours=4), D("0.001")),
                FundingPayment(datetime(2026, 1, 1, 5), D("0.001")),  # noqa: DTZ001
            ],
        )



def test_intrabar_adverse_mark_price_is_included_in_max_drawdown():
    candles, marks = series(["5", "4", "3", "6", "7", "8", "9"])
    baseline = run_futures_sma_crossover(
        candles, marks, fast_period=2, slow_period=3,
        fee_rate=D("0"), slippage_rate=D("0"),
    )
    marks[5] = marks[5].model_copy(update={"low": D("5")})
    stressed = run_futures_sma_crossover(
        candles, marks, fast_period=2, slow_period=3,
        fee_rate=D("0"), slippage_rate=D("0"),
    )
    assert stressed.liquidation_count == 0
    assert stressed.max_drawdown_pct > baseline.max_drawdown_pct


def test_intrabar_funding_credit_cannot_rescue_prior_liquidation():
    candles, marks = series(["5", "4", "3", "6", "7"])
    marks[4] = marks[4].model_copy(update={"low": D("1")})
    payment = FundingPayment(START + timedelta(hours=4, minutes=30), D("-0.1"))
    result = run_futures_sma_crossover(
        candles, marks, fast_period=2, slow_period=3,
        fee_rate=D("0"), slippage_rate=D("0"),
        funding_payments=[payment],
    )
    assert result.liquidation_count == 1
    assert result.total_funding == D("0")
    assert result.trades[0].exit_reason == "liquidation"
