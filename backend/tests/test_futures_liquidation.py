from decimal import Decimal

import pytest

from app.backtest.futures_liquidation import estimate_liquidation_price
from app.backtest.futures_position import IsolatedPosition

D = Decimal


def position(side="long", margin="500", leverage="2"):
    return IsolatedPosition(side, D("1"), D("1000"), D(leverage), D(margin))


def test_estimate_liquidation_price_long():
    assert estimate_liquidation_price(position(), D("0.005")) == D("502.5125628140703517587939699")


def test_estimate_liquidation_price_short():
    result = estimate_liquidation_price(position("short"), D("0.005"))
    assert result == D("1492.537313432835820895522388")


def test_liquidation_estimate_rejects_invalid_mmr():
    with pytest.raises(ValueError, match="maintenance_margin_rate"):
        estimate_liquidation_price(position(), D("1"))


def test_liquidation_price_never_negative():
    p = position(margin="2000")
    assert estimate_liquidation_price(p, D("0.01")) == D("0")
