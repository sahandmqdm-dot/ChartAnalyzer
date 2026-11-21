from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.market_data.bybit import BybitSpotAdapter
from app.market_data.provider import MarketDataProviderError


def bybit_row(timestamp: int, close: str = "11"):
    return [str(timestamp), "10", "12", "9", close, "5", "55"]


def response(rows):
    return {"retCode": 0, "retMsg": "OK", "result": {"category": "spot", "symbol": "BTCUSDT", "list": rows}}


@pytest.mark.asyncio
async def test_bybit_adapter_sorts_reverse_response_and_normalizes_candles():
    rows = [bybit_row(60_000), bybit_row(0)]
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=response(rows)))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await BybitSpotAdapter(
            client, clock=lambda: datetime.fromtimestamp(999, tz=UTC)
        ).fetch_ohlcv("btcusdt", "1m", limit=2)
    assert [item.timestamp.timestamp() for item in result] == [0, 60]
    assert result[0].close == Decimal(11)
    assert result[0].close_time == datetime.fromtimestamp(59.999, tz=UTC)
    assert result[0].is_closed is True


@pytest.mark.asyncio
async def test_bybit_adapter_sends_spot_query_parameters():
    seen = {}
    def handler(request):
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=response([bybit_row(0)]))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await BybitSpotAdapter(client).fetch_ohlcv("ETHUSDT", "1h", limit=25)
    assert seen == {"category": "spot", "symbol": "ETHUSDT", "interval": "60", "limit": "25"}


@pytest.mark.asyncio
async def test_bybit_adapter_rejects_unsupported_three_day_interval():
    with pytest.raises(ValueError, match="3d"):
        await BybitSpotAdapter().fetch_ohlcv("BTCUSDT", "3d")


@pytest.mark.asyncio
async def test_bybit_adapter_rejects_exchange_error_payload():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"retCode": 10001, "retMsg": "bad request"})
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(MarketDataProviderError, match="unsuccessful"):
            await BybitSpotAdapter(client).fetch_ohlcv("BTCUSDT")


@pytest.mark.asyncio
async def test_bybit_historical_fetch_pages_backward_and_returns_ascending():
    starts = []
    def handler(request):
        end = int(request.url.params["end"])
        starts.append(end)
        if end >= 120_000:
            return httpx.Response(200, json=response([bybit_row(120_000), bybit_row(60_000)]))
        return httpx.Response(200, json=response([bybit_row(0)]))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        candles = await BybitSpotAdapter(client).fetch_historical_ohlcv(
            "BTCUSDT",
            "1m",
            start_time=datetime.fromtimestamp(0, tz=UTC),
            end_time=datetime.fromtimestamp(180, tz=UTC),
            page_limit=2,
            max_candles=10,
        )
    assert [item.timestamp.timestamp() for item in candles] == [0, 60, 120]
    assert starts == [180_000, 59_999]


@pytest.mark.asyncio
async def test_bybit_adapter_retries_rate_limit():
    calls = 0
    delays = []
    async def sleep(delay):
        delays.append(delay)
    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0.5"})
        return httpx.Response(200, json=response([bybit_row(0)]))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await BybitSpotAdapter(client, sleep=sleep).fetch_ohlcv("BTCUSDT")
    assert len(result) == 1
    assert calls == 2
    assert delays == [0.5]


@pytest.mark.asyncio
async def test_bybit_adapter_rejects_unsupported_eight_hour_interval():
    with pytest.raises(ValueError, match="8h"):
        await BybitSpotAdapter().fetch_ohlcv("BTCUSDT", "8h")
