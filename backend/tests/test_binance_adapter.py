from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.market_data.binance import BinanceSpotAdapter, MarketDataProviderError


def kline(timestamp: int = 1_700_000_000_000):
    return [[timestamp, "10.0", "12.0", "9.0", "11.0", "5.5", timestamp + 60000, "0", 10, "0", "0", "0"]]


@pytest.mark.asyncio
async def test_binance_adapter_parses_candles():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=kline()))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await BinanceSpotAdapter(client).fetch_ohlcv("btcusdt", "1m", limit=10)
    assert len(result) == 1
    assert result[0].open == Decimal("10.0")
    assert result[0].timestamp.tzinfo == UTC


@pytest.mark.asyncio
async def test_adapter_passes_query_parameters():
    seen = {}
    def handler(request):
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=kline())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await BinanceSpotAdapter(client).fetch_ohlcv("ETHUSDT", "5m", limit=25)
    assert seen["symbol"] == "ETHUSDT"
    assert seen["interval"] == "5m"
    assert seen["limit"] == "25"


@pytest.mark.asyncio
async def test_adapter_rejects_invalid_symbol():
    with pytest.raises(ValueError, match="symbol"):
        await BinanceSpotAdapter().fetch_ohlcv("BTC/USDT")


@pytest.mark.asyncio
async def test_adapter_rejects_bad_interval():
    with pytest.raises(ValueError, match="interval"):
        await BinanceSpotAdapter().fetch_ohlcv("BTCUSDT", "17m")


@pytest.mark.asyncio
async def test_adapter_rejects_limit_over_1000():
    with pytest.raises(ValueError, match="limit"):
        await BinanceSpotAdapter().fetch_ohlcv("BTCUSDT", limit=1001)


@pytest.mark.asyncio
async def test_provider_http_error_is_normalized():
    transport = httpx.MockTransport(lambda request: httpx.Response(429))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(MarketDataProviderError, match="request failed"):
            await BinanceSpotAdapter(client).fetch_ohlcv("BTCUSDT")


@pytest.mark.asyncio
async def test_provider_invalid_candle_data_is_rejected():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[[1, "10", "9", "8", "11", "1"]]))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(MarketDataProviderError, match="invalid candle"):
            await BinanceSpotAdapter(client).fetch_ohlcv("BTCUSDT")


@pytest.mark.asyncio
async def test_provider_retries_rate_limit_and_honors_retry_after():
    calls = 0
    delays = []

    async def sleep(delay):
        delays.append(delay)

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json=kline())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await BinanceSpotAdapter(client, sleep=sleep).fetch_ohlcv("BTCUSDT")

    assert len(result) == 1
    assert calls == 2
    assert delays == [1.0]


@pytest.mark.asyncio
async def test_provider_retries_temporary_server_error_with_backoff():
    calls = 0
    delays = []

    async def sleep(delay):
        delays.append(delay)

    def handler(request):
        nonlocal calls
        calls += 1
        if calls < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=kline())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await BinanceSpotAdapter(
            client, retry_base_delay=0.1, sleep=sleep
        ).fetch_ohlcv("BTCUSDT")

    assert len(result) == 1
    assert calls == 3
    assert delays == [0.1, 0.2]


@pytest.mark.asyncio
async def test_provider_does_not_retry_client_errors():
    calls = 0
    delays = []

    async def sleep(delay):
        delays.append(delay)

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(400)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(MarketDataProviderError, match="request failed"):
            await BinanceSpotAdapter(client, sleep=sleep).fetch_ohlcv("BTCUSDT")

    assert calls == 1
    assert delays == []


def test_adapter_rejects_negative_retry_configuration():
    with pytest.raises(ValueError, match="max_retries"):
        BinanceSpotAdapter(max_retries=-1)
    with pytest.raises(ValueError, match="retry_base_delay"):
        BinanceSpotAdapter(retry_base_delay=-0.1)


@pytest.mark.asyncio
async def test_binance_marks_candle_open_before_close_time():
    open_time = 1_700_000_000_000
    close_time = open_time + 60_000
    now = datetime.fromtimestamp(
        (close_time - 1) / 1000, tz=UTC
    )
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json=kline(timestamp=open_time)
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await BinanceSpotAdapter(client, clock=lambda: now).fetch_ohlcv(
            "BTCUSDT", "1m"
        )

    assert result[0].is_closed is False
    assert result[0].close_time.timestamp() == close_time / 1000


@pytest.mark.asyncio
async def test_binance_marks_candle_closed_after_close_time():
    open_time = 1_700_000_000_000
    close_time = open_time + 60_000
    now = datetime.fromtimestamp(close_time / 1000, tz=UTC) + timedelta(milliseconds=1)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=kline(timestamp=open_time))
    )
    async with httpx.AsyncClient(transport=transport) as client:
        result = await BinanceSpotAdapter(client, clock=lambda: now).fetch_ohlcv(
            "BTCUSDT", "1m"
        )

    assert result[0].is_closed is True


def kline_row(timestamp: int):
    return [
        timestamp, "10.0", "12.0", "9.0", "11.0", "5.5",
        timestamp + 59_999, "0", 10, "0", "0", "0",
    ]


@pytest.mark.asyncio
async def test_historical_pagination_fetches_multiple_pages_in_order():
    requested_starts = []

    def handler(request):
        start = int(request.url.params["startTime"])
        requested_starts.append(start)
        if start == 0:
            return httpx.Response(
                200, json=[kline_row(0), kline_row(60_000)]
            )
        if start == 120_000:
            return httpx.Response(200, json=[kline_row(120_000)])
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        result = await BinanceSpotAdapter(client).fetch_historical_ohlcv(
            "BTCUSDT",
            "1m",
            start_time=datetime.fromtimestamp(0, tz=UTC),
            end_time=datetime.fromtimestamp(180_000 / 1000, tz=UTC),
            page_limit=2,
            max_candles=10,
        )

    assert [c.timestamp.timestamp() for c in result] == [0, 60, 120]
    assert requested_starts == [0, 120_000]


@pytest.mark.asyncio
async def test_historical_pagination_respects_max_candles():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=[kline_row(0), kline_row(60_000), kline_row(120_000)],
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        result = await BinanceSpotAdapter(client).fetch_historical_ohlcv(
            "BTCUSDT",
            "1m",
            start_time=datetime.fromtimestamp(0, tz=UTC),
            end_time=datetime.fromtimestamp(600_000 / 1000, tz=UTC),
            page_limit=3,
            max_candles=2,
        )

    assert len(result) == 2
    assert calls == 1


@pytest.mark.asyncio
async def test_historical_pagination_rejects_non_advancing_page():
    def handler(request):
        return httpx.Response(
            200, json=[kline_row(0), kline_row(60_000)]
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        adapter = BinanceSpotAdapter(client)
        adapter._get_with_retries = adapter._get_with_retries
        with pytest.raises(ValueError, match="page_limit"):
            await adapter.fetch_historical_ohlcv(
                "BTCUSDT",
                "1m",
                start_time=datetime.fromtimestamp(0, tz=UTC),
                end_time=datetime.fromtimestamp(600_000 / 1000, tz=UTC),
                page_limit=1001,
            )
