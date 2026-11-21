from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.backtest.engine import run_sma_crossover
from app.market_data.candle import Candle


def make_candles(closes: list[int]) -> list[Candle]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    candles = []
    for index, close in enumerate(closes):
        price = Decimal(close)
        candles.append(
            Candle(
                timestamp=start + timedelta(hours=index),
                close_time=start + timedelta(hours=index + 1) - timedelta(milliseconds=1),
                is_closed=True,
                open=price,
                high=price + Decimal(1),
                low=price - Decimal(1),
                close=price,
                volume=Decimal(10),
            )
        )
    return candles


def test_sma_crossover_executes_on_next_bar_and_closes_at_end():
    candles = make_candles([5, 4, 3, 4, 5, 6, 8])
    result = run_sma_crossover(
        candles,
        fast_period=2,
        slow_period=3,
        initial_equity=Decimal("1000"),
        fee_rate=Decimal("0"),
        slippage_rate=Decimal("0"),
    )

    assert result.trade_count == 1
    assert result.trades[0].entry_time == candles[5].timestamp.isoformat()
    assert result.trades[0].entry_price == Decimal(6)
    assert result.trades[0].exit_time == candles[-1].timestamp.isoformat()
    assert result.trades[0].exit_price == Decimal(8)
    assert result.final_equity == Decimal("1000") * Decimal(8) / Decimal(6)
    assert result.winning_trades == 1


def test_sma_crossover_does_not_use_an_open_candle():
    candles = make_candles([5, 4, 3, 4, 5, 6, 8])
    candles[-1] = Candle(**{**candles[-1].model_dump(), "is_closed": False})
    with pytest.raises(ValueError, match="closed candles"):
        run_sma_crossover(candles, fast_period=2, slow_period=3)


def test_sma_crossover_rejects_unsorted_candles():
    candles = make_candles([5, 4, 3, 4, 5, 6, 8])
    candles[2], candles[3] = candles[3], candles[2]
    with pytest.raises(ValueError, match="strictly increasing"):
        run_sma_crossover(candles, fast_period=2, slow_period=3)


@pytest.mark.parametrize(
    ("fast", "slow"),
    [(0, 3), (3, 3), (4, 3)],
)
def test_sma_crossover_rejects_invalid_periods(fast, slow):
    with pytest.raises(ValueError, match="periods"):
        run_sma_crossover(make_candles([5, 4, 3, 4, 5, 6, 8]), fast_period=fast, slow_period=slow)


def test_sma_crossover_requires_sufficient_history():
    with pytest.raises(ValueError, match="not enough candles"):
        run_sma_crossover(make_candles([5, 4, 3, 4]), fast_period=2, slow_period=3)


def test_sma_crossover_reports_no_trade_without_cross():
    candles = make_candles([5, 6, 7, 8, 9, 10, 11])
    result = run_sma_crossover(
        candles, fast_period=2, slow_period=3, fee_rate=Decimal(0), slippage_rate=Decimal(0)
    )
    assert result.trade_count == 0
    assert result.final_equity == result.initial_equity
    assert result.net_return_pct == Decimal(0)
