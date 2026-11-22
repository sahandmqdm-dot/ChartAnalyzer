from decimal import Decimal

from app.market_data.futures_stream import (
    BinanceFuturesMarketStream,
    normalize_binance_market_event,
)


def test_mark_price_event_normalizes_funding_and_times():
    event = normalize_binance_market_event({
        "e": "markPriceUpdate",
        "E": 1_800_000_000_000,
        "s": "BTCUSDT",
        "p": "101.25",
        "i": "101.00",
        "r": "0.0001",
        "T": 1_800_003_600_000,
    }, "BTCUSDT")
    assert event is not None
    assert event["mark_price"] == Decimal("101.25")
    assert event["index_price"] == Decimal("101.00")
    assert event["funding_rate"] == Decimal("0.0001")
    assert event["next_funding_time"].timestamp() == 1_800_003_600


def test_combined_book_ticker_and_trade_events_are_supported():
    book = normalize_binance_market_event({
        "stream": "btcusdt@bookTicker",
        "data": {
            "e": "bookTicker", "E": 1_800_000_000_000, "s": "BTCUSDT",
            "b": "100", "B": "2", "a": "101", "A": "3",
        },
    }, "BTCUSDT")
    trade = normalize_binance_market_event({
        "e": "aggTrade", "E": 1_800_000_001_000, "T": 1_800_000_000_900,
        "s": "BTCUSDT", "p": "100.5", "q": "0.25", "m": False,
    }, "BTCUSDT")
    assert book is not None and book["best_bid"] == Decimal("100")
    assert book["best_ask_quantity"] == Decimal("3")
    assert trade is not None and trade["last_trade_price"] == Decimal("100.5")
    assert trade["last_trade_quantity"] == Decimal("0.25")


def test_market_event_rejects_wrong_symbol_and_invalid_prices():
    assert normalize_binance_market_event({
        "e": "markPriceUpdate", "E": 1, "s": "ETHUSDT",
        "p": "100", "i": "99", "r": "0", "T": 2,
    }, "BTCUSDT") is None
    assert normalize_binance_market_event({
        "e": "bookTicker", "E": 1, "s": "BTCUSDT",
        "b": "0", "B": "2", "a": "101", "A": "3",
    }, "BTCUSDT") is None
    assert normalize_binance_market_event({"e": "unknown"}, "BTCUSDT") is None


def test_stream_keeps_latest_snapshot_and_starts_stopped():
    stream = BinanceFuturesMarketStream("btcusdt")
    assert stream.snapshot()["state"] == "stopped"
    assert stream.ingest({
        "e": "markPriceUpdate", "E": 1_800_000_000_000,
        "s": "BTCUSDT", "p": "100", "i": "99.9", "r": "0.001",
        "T": 1_800_003_600_000,
    })
    snapshot = stream.snapshot()
    assert snapshot["symbol"] == "BTCUSDT"
    assert snapshot["mark_price"] == "100"
    assert snapshot["funding_rate"] == "0.001"
    assert snapshot["state"] == "stopped"
