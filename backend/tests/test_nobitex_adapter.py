from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.market_data.nobitex import NobitexSpotAdapter
from app.market_data.provider import MarketDataProviderError


def udf_payload(timestamps=(0, 60)):
    return {
        "s": "ok",
        "t": list(timestamps),
        "o": [10, 11][:len(timestamps)],
        "h": [12, 13][:len(timestamps)],
        "l": [9, 10][:len(timestamps)],
        "c": [11, 12][:len(timestamps)],
        "v": [5, 6][:len(timestamps)],
    }


@pytest.mark.asyncio
async def test_nobitex_adapter_parses_udf_candles_and_query():
    seen = {}
    def handler(request):
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=udf_payload((0,)))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await NobitexSpotAdapter(
            client, clock=lambda: datetime.fromtimestamp(120, tz=UTC)
        ).fetch_ohlcv("bt c", "1m") if False else await NobitexSpotAdapter(
            client, clock=lambda: datetime.fromtimestamp(120, tz=UTC)
        ).fetch_ohlcv("BTCIRT", "1m", limit=1)
    assert len(result) == 1
    assert result[0].open == Decimal("10")
    assert result[0].timestamp == datetime.fromtimestamp(0, tz=UTC)
    assert result[0].is_closed is True
    assert seen["symbol"] == "BTCIRT"
    assert seen["resolution"] == "1"
    assert seen["countback"] == "1"


@pytest.mark.asyncio
async def test_nobitex_no_data_returns_empty_list():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"s": "no_data"}))
    async with httpx.AsyncClient(transport=transport) as client:
        assert await NobitexSpotAdapter(client).fetch_ohlcv("BTCIRT") == []


@pytest.mark.asyncio
async def test_nobitex_rejects_malformed_parallel_arrays():
    payload = {"s": "ok", "t": [0], "o": [10], "h": [12], "l": [9], "c": [], "v": [1]}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(MarketDataProviderError, match="invalid OHLC"):
            await NobitexSpotAdapter(client).fetch_ohlcv("BTCIRT")


@pytest.mark.asyncio
async def test_nobitex_historical_pages_and_sorts_candles():
    requested = []
    def handler(request):
        page = int(request.url.params["page"])
        requested.append(page)
        if page == 1:
            return httpx.Response(200, json=udf_payload((60, 0)))
        if page == 2:
            return httpx.Response(200, json=udf_payload((120,)))
        return httpx.Response(200, json={"s": "no_data"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        candles = await NobitexSpotAdapter(client).fetch_historical_ohlcv(
            "BTCIRT", "1m",
            start_time=datetime.fromtimestamp(0, tz=UTC),
            end_time=datetime.fromtimestamp(180, tz=UTC),
            page_limit=2,
        )
    assert [item.timestamp.timestamp() for item in candles] == [0, 60, 120]
    assert requested == [1, 2]


def test_nobitex_rejects_unsupported_interval_and_large_page():
    with pytest.raises(ValueError, match="interval"):
        import asyncio
        asyncio.run(NobitexSpotAdapter().fetch_ohlcv("BTCIRT", "2h"))
    with pytest.raises(ValueError, match="page_limit"):
        import asyncio
        asyncio.run(NobitexSpotAdapter().fetch_historical_ohlcv(
            "BTCIRT", "1m",
            start_time=datetime.fromtimestamp(0, tz=UTC),
            end_time=datetime.fromtimestamp(60, tz=UTC),
            page_limit=501,
        ))
