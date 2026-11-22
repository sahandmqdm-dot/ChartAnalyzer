from decimal import Decimal

import pytest

from app.backtest.futures_position import IsolatedPosition

D = Decimal


def make_position(side="long", quantity="0.5", entry="40000", leverage="5", margin="4000"):
    return IsolatedPosition(side, D(quantity), D(entry), D(leverage), D(margin))


def test_position_notional_and_effective_leverage():
    p = make_position()
    assert p.notional == D("20000")
    assert p.effective_leverage == D("5")


@pytest.mark.parametrize(
    ("side", "mark", "expected"),
    [("long", "42000", "1000"), ("long", "38000", "-1000"),
     ("short", "38000", "1000"), ("short", "42000", "-1000")],
)
def test_unrealized_pnl_by_side(side, mark, expected):
    assert make_position(side=side).unrealized_at(D(mark)) == D(expected)


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"side": "buy"}, "side"),
        ({"quantity": "0"}, "quantity"),
        ({"entry": "-1"}, "entry_price"),
        ({"leverage": "0.5"}, "leverage"),
        ({"margin": "0"}, "isolated_margin"),
        ({"leverage": "NaN"}, "leverage"),
    ],
)
def test_rejects_invalid_position(kwargs, error):
    with pytest.raises(ValueError, match=error):
        make_position(**kwargs)


def test_mark_price_must_be_positive_and_finite():
    p = make_position()
    with pytest.raises(ValueError, match="mark_price"):
        p.unrealized_at(D("0"))
    with pytest.raises(ValueError, match="finite"):
        p.unrealized_at(D("Infinity"))
