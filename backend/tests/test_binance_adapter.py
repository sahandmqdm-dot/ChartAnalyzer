from datetime import UTC, datetime
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
