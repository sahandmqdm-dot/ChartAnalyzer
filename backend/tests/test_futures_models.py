from decimal import Decimal

import pytest

from app.backtest.futures_models import IsolatedPosition, open_isolated_position


def test_position_quantity_is_margin_times_leverage_over_entry_price():
    position = open_isolated_position(
        side="long",
        margin=Decimal("100"),
        leverage=10,
        entry_price=Decimal("20000"),
        fee_rate=Decimal("0.001"),
    )
    assert position.quantity == Decimal("0.05")
    assert position.allocated_margin == Decimal("100")
    assert position.entry_fee == Decimal("1")
    assert position.leverage == 10


def test_short_position_uses_same_linear_quantity_formula():
    position = open_isolated_position(
        side="short", margin=Decimal("50"), leverage=5, entry_price=Decimal("100")
    )
    assert position.quantity == Decimal("2.5")
    assert position.side == "short"


@pytest.mark.parametrize("leverage", [0, -1, 126, True])
def test_invalid_leverage_rejected(leverage):
    with pytest.raises(ValueError, match="leverage"):
        open_isolated_position(
            side="long", margin=Decimal("100"), leverage=leverage, entry_price=Decimal("100")
        )


def test_invalid_position_side_rejected():
    with pytest.raises(ValueError, match="side"):
        open_isolated_position(
            side="buy", margin=Decimal("100"), leverage=2, entry_price=Decimal("100")
        )


def test_direct_position_creation_validates_fields():
    with pytest.raises(ValueError, match="quantity"):
        IsolatedPosition(
            side="long",
            quantity=Decimal("0"),
            entry_price=Decimal("100"),
            leverage=1,
            allocated_margin=Decimal("10"),
            entry_fee=Decimal("0"),
        )
