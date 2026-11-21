from decimal import Decimal

import httpx
import pytest

from app.market_data.futures import FuturesMarketDataError
from app.market_data.mark_price import (
    BinanceUsdmMarkPriceAdapter,
    BybitLinearMarkPriceAdapter,
)


@pytest.mark.asyncio
async def test_binance_mark_price_klines_are_normalized_and_sorted():
    def handler(request):
        assert request.url.path.endswith("/markPriceKlines")
        assert request.url.params["symbol"] == "BTCUSDT"
        assert request.url.params["interval"] == "1h"
        return httpx.Response(200, json=[
            [1_800_003_600_000, "102", "104", "101", "103", "0", 1_800_007_199_999],
            [1_800_000_000_000, "100", "103", "99", "102", "0", 1_800_003_599_999],
        ])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await BinanceUsdmMarkPriceAdapter(client).fetch_mark_price_history(
            "btcusdt", "1h", 20
        )

    assert result.provider == "binance_usdm_futures"
    assert result.candles[0].open == Decimal(100)
    assert result.candles[1].close == Decimal(103)
    assert result.candles[0].timestamp < result.candles[1].timestamp


@pytest.mark.asyncio
async def test_bybit_mark_price_klines_are_normalized():
    def handler(request):
        assert request.url.path.endswith("/mark-price-kline")
        assert request.url.params["category"] == "linear"
        assert request.url.params["interval"] == "60"
        return httpx.Response(200, json={
            "retCode": 0,
            "result": {"list": [
                ["1800000000000", "100", "103", "99", "102", "0", "0"]
            ]},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await BybitLinearMarkPriceAdapter(client).fetch_mark_price_history(
            "BTCUSDT", "1h", 10
        )

    assert result.symbol == "BTCUSDT"
    assert result.candles[0].open == Decimal(100)
    assert result.candles[0].close == Decimal(102)


@pytest.mark.asyncio
async def test_mark_price_adapter_rejects_non_finite_and_invalid_ohlc():
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, json=[[1_800_000_000_000, "NaN", "103", "99", "102", "0", 0]]
    ))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(FuturesMarketDataError, match="invalid mark price"):
            await BinanceUsdmMarkPriceAdapter(client).fetch_mark_price_history(
                "BTCUSDT", "1h", 5
            )


@pytest.mark.asyncio
async def test_bybit_mark_price_adapter_rejects_exchange_error():
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, json={"retCode": 10001, "retMsg": "invalid symbol"}
    ))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(FuturesMarketDataError, match="unsuccessful"):
            await BybitLinearMarkPriceAdapter(client).fetch_mark_price_history(
                "BTCUSDT", "1h", 5
            )


@pytest.mark.asyncio
async def test_mark_price_adapter_rejects_bad_period_and_limit():
    with pytest.raises(ValueError, match="period"):
        await BinanceUsdmMarkPriceAdapter().fetch_mark_price_history("BTCUSDT", "2m", 5)
    with pytest.raises(ValueError, match="limit"):
        await BinanceUsdmMarkPriceAdapter().fetch_mark_price_history("BTCUSDT", "1h", 0)
