from decimal import Decimal

from app.market_data.liquidation_stream import (
    LiquidationCollector,
    parse_binance_liquidation,
    parse_bybit_liquidations,
)


def test_binance_force_order_normalizes_long_liquidation_and_execution_notional():
    event = parse_binance_liquidation({
        "e": "forceOrder",
        "E": 1730000000000,
        "o": {
            "s": "BTCUSDT", "S": "SELL", "q": "0.5", "l": "0.2",
            "p": "60000", "ap": "60100", "T": 1730000000000,
        },
    })
    assert event is not None
    assert event.provider == "binance_usdm_futures"
    assert event.liquidation_side == "long"
    assert event.order_side == "SELL"
    assert event.quantity == Decimal("0.2")
    assert event.price == Decimal("60100")
    assert event.notional == Decimal("12020")
    assert event.price_kind == "average_execution"


def test_binance_force_order_maps_buy_to_short_and_ignores_invalid_payload():
    event = parse_binance_liquidation({
        "e": "forceOrder",
        "E": 1730000000000,
        "o": {"s": "BTCUSDT", "S": "BUY", "q": "1", "p": "50000", "T": 1730000000000},
    })
    assert event is not None
    assert event.liquidation_side == "short"
    assert event.quantity == 1
    assert parse_binance_liquidation({"e": "aggTrade"}) is None
    assert parse_binance_liquidation({
        "e": "forceOrder", "E": 1730000000000,
        "o": {"s": "BTCUSDT", "S": "SELL", "q": "0", "p": "1"},
    }) is None


def test_bybit_all_liquidation_maps_exchange_side_and_bankruptcy_price():
    events = parse_bybit_liquidations({
        "topic": "allLiquidation.BTCUSDT",
        "data": [
            {"T": 1730000000000, "s": "BTCUSDT", "S": "Buy", "v": "2", "p": "59000"},
            {"T": 1730000001000, "s": "BTCUSDT", "S": "Sell", "v": "3", "p": "61000"},
        ],
    })
    assert len(events) == 2
    assert events[0].liquidation_side == "long"
    assert events[0].price_kind == "bankruptcy"
    assert events[0].notional == 118000
    assert events[1].liquidation_side == "short"
    assert parse_bybit_liquidations({"topic": "tickers.BTCUSDT", "data": []}) == []


def test_collector_deduplicates_and_bounds_recent_events():
    collector = LiquidationCollector("binance_usdm_futures", "BTCUSDT", max_events=1)
    first = parse_binance_liquidation({
        "e": "forceOrder", "E": 1730000000000,
        "o": {"s": "BTCUSDT", "S": "SELL", "q": "1", "p": "100", "T": 1730000000000},
    })
    second = parse_binance_liquidation({
        "e": "forceOrder", "E": 1730000001000,
        "o": {"s": "BTCUSDT", "S": "BUY", "q": "1", "p": "101", "T": 1730000001000},
    })
    assert first is not None and second is not None
    collector.add(first)
    collector.add(first)
    assert len(collector.events) == 1
    collector.add(second)
    assert len(collector.events) == 1
    assert collector.events[0].event_id == second.event_id
    assert collector.snapshot()["status"]["event_count"] == 1
