from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.backtest.futures_mark_price import simulate_mark_price_liquidation
from app.backtest.futures_position import IsolatedPosition
from app.market_data.mark_price import MarkPriceCandle

D = Decimal
START = datetime(2026, 1, 1, tzinfo=UTC)


def position(side="long"):
    return IsolatedPosition(side, D("1"), D("1000"), D("2"), D("500"))


def candle(offset, *, opening="1000", high="1010", low="990", close="1000", closed=True):
    timestamp = START + timedelta(minutes=offset)
    return MarkPriceCandle(
        timestamp=timestamp,
        close_time=timestamp + timedelta(minutes=1),
        is_closed=closed,
        open=D(opening), high=D(high), low=D(low), close=D(close),
    )


def test_long_liquidates_when_mark_low_touches_threshold():
    event = simulate_mark_price_liquidation(
        position(), [candle(0, low="500")], D("0.005")
    )
    assert event.liquidated is True
    assert event.price == D("502.5125628140703517587939698")
    assert event.processed_candles == 1


def test_short_liquidates_when_mark_high_touches_threshold():
    event = simulate_mark_price_liquidation(
        position("short"), [candle(0, high="1500")], D("0.005")
    )
    assert event.liquidated is True
    assert event.price == D("1492.537313432835820895522388")


def test_gap_through_threshold_fills_at_open():
    event = simulate_mark_price_liquidation(
        position(), [candle(0, opening="490", high="500", low="480", close="495")],
        D("0.005"),
    )
    assert event.price == D("490")


def test_liquidation_fee_reduces_remaining_isolated_equity():
    event = simulate_mark_price_liquidation(
        position(), [candle(0, low="500")], D("0.005"), D("0.01")
    )
    assert event.liquidation_fee == event.price * D("0.01")
    assert event.remaining_isolated_equity == max(
        D("0"), D("500") + event.gross_pnl - event.liquidation_fee
    )


def test_no_liquidation_returns_original_margin():
    event = simulate_mark_price_liquidation(
        position(), [candle(0), candle(1)], D("0.005")
    )
    assert event.liquidated is False
    assert event.processed_candles == 2
    assert event.remaining_isolated_equity == D("500")


def test_open_candle_stops_simulation_without_reading_future_candles():
    event = simulate_mark_price_liquidation(
        position(),
        [candle(0, closed=False), candle(1, low="400")],
        D("0.005"),
    )
    assert event.liquidated is False
    assert event.processed_candles == 0


def test_out_of_order_candles_are_rejected():
    with pytest.raises(ValueError, match="chronological"):
        simulate_mark_price_liquidation(
            position(), [candle(1), candle(0)], D("0.005")
        )


def test_invalid_fee_rate_is_rejected():
    with pytest.raises(ValueError, match="liquidation_fee_rate"):
        simulate_mark_price_liquidation(position(), [], D("0.005"), D("1"))


def test_received_funding_lowers_long_liquidation_threshold():
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    position = IsolatedPosition("long", D("1"), D("1000"), D("2"), D("500"))
    candle = MarkPriceCandle(
        timestamp=timestamp,
        close_time=timestamp + timedelta(minutes=5),
        is_closed=True,
        open=D("1000"), high=D("1001"), low=D("498"), close=D("500"),
    )
    baseline = simulate_mark_price_liquidation(position, [candle], D("0.005"))
    funded = simulate_mark_price_liquidation(
        position, [candle], D("0.005"), margin_adjustment=D("10")
    )
    assert baseline.liquidated
    assert not funded.liquidated


def test_paid_funding_raises_long_liquidation_threshold():
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    position = IsolatedPosition("long", D("1"), D("1000"), D("2"), D("500"))
    candle = MarkPriceCandle(
        timestamp=timestamp,
        close_time=timestamp + timedelta(minutes=5),
        is_closed=True,
        open=D("1000"), high=D("1001"), low=D("510"), close=D("515"),
    )
    baseline = simulate_mark_price_liquidation(position, [candle], D("0.005"))
    funded = simulate_mark_price_liquidation(
        position, [candle], D("0.005"), margin_adjustment=D("-10")
    )
    assert not baseline.liquidated
    assert funded.liquidated



def test_long_gap_reports_bankruptcy_price_and_uncovered_gap_loss():
    event = simulate_mark_price_liquidation(
        position(),
        [candle(0, opening="490", high="500", low="480", close="495")],
        D("0.005"),
    )
    assert event.liquidated
    assert event.liquidation_threshold < event.bankruptcy_price
    assert event.bankruptcy_price == D("500")
    assert event.price == D("490")
    assert event.uncovered_gap_loss == D("10")


def test_short_gap_reports_uncovered_loss_beyond_bankruptcy():
    event = simulate_mark_price_liquidation(
        position("short"),
        [candle(0, opening="1510", high="1520", low="1505", close="1515")],
        D("0.005"),
    )
    assert event.liquidated
    assert event.bankruptcy_price == D("1500")
    assert event.price == D("1510")
    assert event.uncovered_gap_loss == D("10")


def test_bankruptcy_price_moves_with_funding_but_not_liquidation_fee_reserve():
    baseline = simulate_mark_price_liquidation(position(), [], D("0.005"))
    funded = simulate_mark_price_liquidation(
        position(), [], D("0.005"), margin_adjustment=D("10")
    )
    fee_reserved = simulate_mark_price_liquidation(
        position(), [], D("0.005"), liquidation_fee_rate=D("0.02")
    )
    assert baseline.bankruptcy_price == D("500")
    assert funded.bankruptcy_price == D("510")
    assert fee_reserved.bankruptcy_price == baseline.bankruptcy_price
    assert fee_reserved.liquidation_threshold > baseline.liquidation_threshold
