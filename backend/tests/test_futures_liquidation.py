from decimal import Decimal

import pytest

from app.backtest.futures_liquidation import estimate_liquidation_price
from app.backtest.futures_position import IsolatedPosition

D = Decimal


def position(side="long", margin="500", leverage="2"):
    return IsolatedPosition(side, D("1"), D("1000"), D(leverage), D(margin))


def test_estimate_liquidation_price_long():
    assert estimate_liquidation_price(position(), D("0.005")) == D("502.5125628140703517587939698")


def test_estimate_liquidation_price_short():
    result = estimate_liquidation_price(position("short"), D("0.005"))
    assert result == D("1492.537313432835820895522388")


def test_liquidation_estimate_rejects_invalid_mmr():
    with pytest.raises(ValueError, match="maintenance_margin_rate"):
        estimate_liquidation_price(position(), D("1"))


def test_liquidation_price_never_negative():
    p = position(margin="2000")
    assert estimate_liquidation_price(p, D("0.01")) == D("0")


def test_funding_adjustment_moves_long_liquidation_threshold():
    p = position()
    baseline = estimate_liquidation_price(p, D("0.005"))
    received_funding = estimate_liquidation_price(p, D("0.005"), D("10"))
    paid_funding = estimate_liquidation_price(p, D("0.005"), D("-10"))
    assert received_funding < baseline
    assert paid_funding > baseline


def test_funding_adjustment_moves_short_liquidation_threshold():
    p = position("short")
    baseline = estimate_liquidation_price(p, D("0.005"))
    received_funding = estimate_liquidation_price(p, D("0.005"), D("10"))
    paid_funding = estimate_liquidation_price(p, D("0.005"), D("-10"))
    assert received_funding > baseline
    assert paid_funding < baseline


def test_funding_adjustment_must_be_finite_decimal():
    with pytest.raises(ValueError, match="margin_adjustment"):
        estimate_liquidation_price(position(), D("0.005"), D("NaN"))



def test_liquidation_fee_reserve_moves_threshold_earlier_for_both_sides():
    long_position = position("long")
    short_position = position("short")
    long_without_fee = estimate_liquidation_price(long_position, D("0.005"))
    long_with_fee = estimate_liquidation_price(
        long_position, D("0.005"), liquidation_fee_rate=D("0.01")
    )
    short_without_fee = estimate_liquidation_price(short_position, D("0.005"))
    short_with_fee = estimate_liquidation_price(
        short_position, D("0.005"), liquidation_fee_rate=D("0.01")
    )
    assert long_with_fee > long_without_fee
    assert short_with_fee < short_without_fee


def test_liquidation_fee_reserve_rejects_incompatible_rates():
    with pytest.raises(ValueError, match="plus liquidation_fee_rate"):
        estimate_liquidation_price(
            position(), D("0.6"), liquidation_fee_rate=D("0.4")
        )
