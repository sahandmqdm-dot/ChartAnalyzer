from decimal import Decimal

from app.market_data.futures_stream import (
    BinanceFuturesMarketStream,
    BybitLinearFuturesMarketStream,
    normalize_binance_market_event,
    normalize_bybit_market_event,
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


def test_bybit_ticker_normalizes_mark_funding_open_interest_and_top_of_book():
    event = normalize_bybit_market_event(
        {
            "topic": "tickers.BTCUSDT",
            "type": "snapshot",
            "ts": 1_800_000_000_000,
            "data": [{
                "symbol": "BTCUSDT",
                "markPrice": "101.25",
                "indexPrice": "101.00",
                "fundingRate": "-0.0001",
                "nextFundingTime": "1800003600000",
                "openInterest": "123.4",
                "openInterestValue": "12499.05",
                "bid1Price": "101.20",
                "bid1Size": "2.5",
                "ask1Price": "101.30",
                "ask1Size": "3.5",
            }],
        },
        "BTCUSDT",
    )
    assert event is not None
    assert event["mark_price"] == Decimal("101.25")
    assert event["index_price"] == Decimal("101.00")
    assert event["funding_rate"] == Decimal("-0.0001")
    assert event["open_interest"] == Decimal("123.4")
    assert event["open_interest_value"] == Decimal("12499.05")
    assert event["best_bid"] == Decimal("101.20")
    assert event["best_ask_quantity"] == Decimal("3.5")


def test_bybit_public_trade_uses_latest_trade_in_batch():
    event = normalize_bybit_market_event(
        {
            "topic": "publicTrade.BTCUSDT",
            "ts": 1_800_000_000_100,
            "data": [
                {"T": 1_800_000_000_010, "s": "BTCUSDT", "p": "100", "v": "0.1"},
                {"T": 1_800_000_000_090, "s": "BTCUSDT", "p": "101", "v": "0.2"},
            ],
        },
        "BTCUSDT",
    )
    assert event is not None
    assert event["last_trade_price"] == Decimal("101")
    assert event["last_trade_quantity"] == Decimal("0.2")
    assert event["last_trade_time"].microsecond == 90_000


def test_bybit_stream_exposes_provider_and_latest_ticker_snapshot():
    stream = BybitLinearFuturesMarketStream("btcusdt")
    assert stream.snapshot()["provider"] == "bybit_linear_futures"
    assert stream.ingest(
        {
            "topic": "tickers.BTCUSDT",
            "ts": 1_800_000_000_000,
            "data": [{
                "symbol": "BTCUSDT",
                "markPrice": "100",
                "indexPrice": "99.9",
                "fundingRate": "0.001",
                "nextFundingTime": "1800003600000",
                "openInterest": "200.5",
                "openInterestValue": "20050",
            }],
        }
    )
    snapshot = stream.snapshot()
    assert snapshot["provider"] == "bybit_linear_futures"
    assert snapshot["open_interest"] == "200.5"
    assert snapshot["mark_price"] == "100"


def test_bybit_ticker_rejects_wrong_symbol_and_non_finite_prices():
    assert normalize_bybit_market_event(
        {
            "topic": "tickers.ETHUSDT",
            "ts": 1_800_000_000_000,
            "data": [{"symbol": "ETHUSDT", "markPrice": "100"}],
        },
        "BTCUSDT",
    ) is None
    assert normalize_bybit_market_event(
        {
            "topic": "tickers.BTCUSDT",
            "ts": 1_800_000_000_000,
            "data": [{"symbol": "BTCUSDT", "markPrice": "NaN"}],
        },
        "BTCUSDT",
    ) is None
