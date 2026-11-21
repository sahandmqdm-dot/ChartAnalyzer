import pytest

from app.market_data.symbol import Symbol


def test_symbol_normalizes_case_and_whitespace():
    assert str(Symbol("  btcusdt ")) == "BTCUSDT"


@pytest.mark.parametrize("value", ["", "BTC/USDT", "BTC-USDT", "BTC USDT"])
def test_symbol_rejects_unsupported_characters(value):
    with pytest.raises(ValueError, match="symbol"):
        Symbol(value)
