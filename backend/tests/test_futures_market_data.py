from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.market_data.futures import (
    BinanceUsdmFuturesAdapter,
    BybitLinearFuturesAdapter,
    FuturesMarketDataError,
)


@pytest.mark.asyncio
async def test_binance_futures_adapter_normalizes_mark_funding_and_open_interest():
    def handler(request):
        if request.url.path.endswith("/premiumIndex"):
            return httpx.Response(200, json={
                "symbol": "BTCUSDT",
                "markPrice": "101.5",
                "indexPrice": "101.0",
                "lastFundingRate": "0.0001",
                "nextFundingTime": 1_800_000_000_000,
            })
        return httpx.Response(200, json={"symbol": "BTCUSDT", "openInterest": "12.5"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await BinanceUsdmFuturesAdapter(client).fetch_overview("btcusdt")

    assert result.provider == "binance_usdm_futures"
    assert result.market_type == "linear_perpetual"
    assert result.symbol == "BTCUSDT"
    assert result.mark_price == Decimal("101.5")
    assert result.index_price == Decimal("101.0")
    assert result.funding_rate == Decimal("0.0001")
    assert result.open_interest == Decimal("12.5")
    assert result.open_interest_value == Decimal("1268.75")
    assert result.next_funding_time == datetime.fromtimestamp(
        1_800_000_000, tz=UTC
    )


@pytest.mark.asyncio
async def test_bybit_futures_adapter_normalizes_linear_ticker():
    def handler(request):
        assert request.url.params["category"] == "linear"
        assert request.url.params["symbol"] == "ETHUSDT"
        return httpx.Response(200, json={
            "retCode": 0,
            "result": {"list": [{
                "symbol": "ETHUSDT",
                "markPrice": "3000",
                "indexPrice": "2998",
                "fundingRate": "-0.0002",
                "openInterest": "1500",
                "openInterestValue": "4500000",
                "nextFundingTime": "1800000000000",
            }]},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await BybitLinearFuturesAdapter(client).fetch_overview("ethusdt")

    assert result.provider == "bybit_linear_futures"
    assert result.mark_price == Decimal(3000)
    assert result.index_price == Decimal(2998)
    assert result.funding_rate == Decimal("-0.0002")
    assert result.open_interest == Decimal(1500)
    assert result.open_interest_value == Decimal(4500000)


@pytest.mark.asyncio
async def test_futures_adapter_rejects_invalid_symbol():
    with pytest.raises(ValueError, match="symbol"):
        await BinanceUsdmFuturesAdapter().fetch_overview("BTC/USDT")


@pytest.mark.asyncio
async def test_bybit_futures_adapter_rejects_exchange_error():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"retCode": 10001, "retMsg": "invalid symbol"}
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(FuturesMarketDataError, match="unsuccessful"):
            await BybitLinearFuturesAdapter(client).fetch_overview("BTCUSDT")


@pytest.mark.asyncio
async def test_futures_adapter_normalizes_http_errors():
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(FuturesMarketDataError, match="request failed"):
            await BinanceUsdmFuturesAdapter(client).fetch_overview("BTCUSDT")
