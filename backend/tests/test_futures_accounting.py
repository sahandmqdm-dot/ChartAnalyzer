from decimal import Decimal

import pytest

from app.backtest.futures_accounting import (
    funding_cashflow,
    linear_position_pnl,
    position_notional,
    trading_fee,
)


def test_linear_pnl_long_and_short():
    assert linear_position_pnl("long", Decimal("2"), Decimal("100"), Decimal("125")) == Decimal("50")
    assert linear_position_pnl("short", Decimal("2"), Decimal("100"), Decimal("75")) == Decimal("50")
    assert linear_position_pnl("long", Decimal("2"), Decimal("100"), Decimal("75")) == Decimal("-50")
    assert linear_position_pnl("short", Decimal("2"), Decimal("100"), Decimal("125")) == Decimal("-50")


def test_trading_fee_on_quote_notional():
    assert trading_fee(Decimal("2500"), Decimal("0.0006")) == Decimal("1.5000")


@pytest.mark.parametrize(
    ("side", "rate", "expected"),
    [
        ("long", Decimal("0.01"), Decimal("-10")),
        ("short", Decimal("0.01"), Decimal("10")),
        ("long", Decimal("-0.01"), Decimal("10")),
        ("short", Decimal("-0.01"), Decimal("-10")),
    ],
)
def test_funding_cashflow_direction(side, rate, expected):
    assert funding_cashflow(side, Decimal("1"), Decimal("1000"), rate) == expected


def test_position_notional():
    assert position_notional(Decimal("0.25"), Decimal("40000")) == Decimal("10000")


@pytest.mark.parametrize(
    ("call", "match"),
    [
        (lambda: linear_position_pnl("long", Decimal("0"), Decimal("1"), Decimal("2")), "quantity"),
        (lambda: linear_position_pnl("buy", Decimal("1"), Decimal("1"), Decimal("2")), "side"),
        (lambda: trading_fee(Decimal("10"), Decimal("1")), "fee_rate"),
        (lambda: trading_fee(Decimal("-1"), Decimal("0.01")), "notional"),
        (lambda: position_notional(Decimal("1"), Decimal("NaN")), "price"),
    ],
)
def test_rejects_invalid_accounting_inputs(call, match):
    with pytest.raises(ValueError, match=match):
        call()
