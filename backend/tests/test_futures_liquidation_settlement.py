from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.backtest.futures_lifecycle import FuturesAccount
from app.backtest.futures_mark_price import simulate_mark_price_liquidation
from app.market_data.mark_price import MarkPriceCandle

D = Decimal


def liquidation_event():
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    candle = MarkPriceCandle(
        timestamp=timestamp,
        close_time=timestamp + timedelta(minutes=1),
        is_closed=True,
        open=D("1000"),
        high=D("1010"),
        low=D("490"),
        close=D("500"),
    )
    account = FuturesAccount(D("10000"))
    position = account.open_position(
        side="long",
        quantity=D("1"),
        entry_price=D("1000"),
        leverage=D("2"),
        fee_rate=D("0.001"),
    )
    event = simulate_mark_price_liquidation(
        position, [candle], D("0.005"), D("0.001")
    )
    return account, event


def test_liquidation_settlement_updates_wallet_and_clears_position():
    account, event = liquidation_event()
    assert event.liquidated
    realized = account.settle_liquidation(event)
    expected = event.gross_pnl - D("1") - event.liquidation_fee
    assert realized == expected
    assert account.position is None
    assert account.entry_fee_paid == D("0")
    assert account.funding_total == D("0")
    assert account.wallet_balance == D("9999") + event.gross_pnl - event.liquidation_fee


def test_non_liquidation_event_cannot_be_settled():
    account, _ = liquidation_event()
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    safe_candle = MarkPriceCandle(
        timestamp=timestamp,
        close_time=timestamp + timedelta(minutes=1),
        is_closed=True,
        open=D("1000"), high=D("1010"), low=D("990"), close=D("1000"),
    )
    event = simulate_mark_price_liquidation(
        account.position, [safe_candle], D("0.005")
    )
    with pytest.raises(ValueError, match="does not represent"):
        account.settle_liquidation(event)


def test_liquidation_cannot_be_settled_twice():
    account, event = liquidation_event()
    account.settle_liquidation(event)
    with pytest.raises(ValueError, match="no open"):
        account.settle_liquidation(event)

def test_isolated_gap_loss_is_capped_at_locked_margin():
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    account = FuturesAccount(D("10000"))
    position = account.open_position(
        side="long", quantity=D("1"), entry_price=D("1000"),
        leverage=D("2"), fee_rate=D("0.001"),
    )
    gap_candle = MarkPriceCandle(
        timestamp=timestamp,
        close_time=timestamp + timedelta(minutes=1),
        is_closed=True,
        open=D("1"), high=D("2"), low=D("1"), close=D("1"),
    )
    event = simulate_mark_price_liquidation(
        position, [gap_candle], D("0.005"), D("0.001")
    )
    realized = account.settle_liquidation(event)
    assert realized == D("-501")
    assert account.wallet_balance == D("9499")

def test_funding_received_is_credited_to_isolated_margin_during_gap_liquidation():
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    account = FuturesAccount(D("10000"))
    position = account.open_position(
        side="long", quantity=D("1"), entry_price=D("1000"),
        leverage=D("2"), fee_rate=D("0.001"),
    )
    funding = account.apply_funding(D("1000"), D("-0.001"))
    assert funding == D("1")
    gap_candle = MarkPriceCandle(
        timestamp=timestamp,
        close_time=timestamp + timedelta(minutes=1),
        is_closed=True,
        open=D("1"), high=D("2"), low=D("1"), close=D("1"),
    )
    event = simulate_mark_price_liquidation(
        position, [gap_candle], D("0.005"), D("0.001"),
        margin_adjustment=account.funding_total,
    )
    realized = account.settle_liquidation(event)
    assert realized == D("-501")
    assert account.wallet_balance == D("9499")



def test_liquidation_event_pnl_must_match_current_position():
    account, event = liquidation_event()
    forged = replace(event, gross_pnl=event.gross_pnl + D("1"))
    with pytest.raises(ValueError, match="PnL does not match"):
        account.settle_liquidation(forged)
    assert account.position is not None
