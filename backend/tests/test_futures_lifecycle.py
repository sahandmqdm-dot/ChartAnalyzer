from decimal import Decimal

import pytest

from app.backtest.futures_lifecycle import FuturesAccount

D = Decimal


def account(balance="10000"):
    return FuturesAccount(D(balance))


def test_open_position_locks_margin_and_deducts_entry_fee():
    a = account()
    p = a.open_position(side="long", quantity=D("0.1"), entry_price=D("1000"), leverage=D("5"), fee_rate=D("0.001"))
    assert p.notional == D("100")
    assert p.isolated_margin == D("20")
    assert a.wallet_balance == D("9999.900")
    assert a.locked_margin == D("20")
    assert a.available_balance == D("9979.900")


def test_mark_equity_unrealized_long_short():
    a = account()
    a.open_position(side="long", quantity=D("1"), entry_price=D("100"), leverage=D("2"), fee_rate=D("0"))
    assert a.unrealized_at(D("110")) == D("10")
    assert a.equity_at(D("110")) == D("10010")


def test_funding_applies_to_wallet_and_is_in_close_pnl():
    a = account()
    a.open_position(side="long", quantity=D("1"), entry_price=D("1000"), leverage=D("2"), fee_rate=D("0"))
    paid = a.apply_funding(D("1000"), D("0.001"))
    assert paid == D("-1")
    realized = a.close_position(D("1010"), D("0"))
    assert realized == D("9")
    assert a.wallet_balance == D("10009")
    assert a.position is None


def test_short_receives_positive_rate_funding():
    a = account()
    a.open_position(side="short", quantity=D("1"), entry_price=D("1000"), leverage=D("2"), fee_rate=D("0"))
    assert a.apply_funding(D("1000"), D("0.001")) == D("1")


def test_rejects_double_open_and_close_without_position():
    a = account()
    a.open_position(side="long", quantity=D("1"), entry_price=D("100"), leverage=D("2"), fee_rate=D("0"))
    with pytest.raises(ValueError, match="already"):
        a.open_position(side="short", quantity=D("1"), entry_price=D("100"), leverage=D("2"), fee_rate=D("0"))
    a.close_position(D("100"), D("0"))
    with pytest.raises(ValueError, match="no open"):
        a.close_position(D("100"), D("0"))


def test_insufficient_balance_rejected():
    a = account("10")
    with pytest.raises(ValueError, match="insufficient"):
        a.open_position(side="long", quantity=D("1"), entry_price=D("1000"), leverage=D("2"), fee_rate=D("0.01"))

def test_realized_pnl_includes_entry_and_exit_fees():
    a = account()
    a.open_position(side="long", quantity=D("1"), entry_price=D("1000"), leverage=D("2"), fee_rate=D("0.001"))
    realized = a.close_position(D("1100"), D("0.001"))
    assert realized == D("98.9")
    assert a.wallet_balance == D("10098.9")
