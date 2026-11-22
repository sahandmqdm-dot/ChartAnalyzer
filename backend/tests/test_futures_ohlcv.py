from decimal import Decimal

import httpx
import pytest

from app.market_data.futures import FuturesMarketDataError
from app.market_data.futures_ohlcv import (
    BinanceUsdmFuturesOHLCVAdapter,
    BybitLinearFuturesOHLCVAdapter,
)


@pytest.mark.asyncio
async def test_binance_usdm_contract_klines_use_futures_endpoint_and_normalize():
    def handler(request):
        assert request.url.host == "fapi.binance.com"
        assert request.url.path == "/fapi/v1/klines"
        assert request.url.params["symbol"] == "BTCUSDT"
        assert request.url.params["interval"] == "1h"
        return httpx.Response(200, json=[
            [1_800_003_600_000, "102", "104", "101", "103", "12.5", 1_800_007_199_999],
            [1_800_000_000_000, "100", "103", "99", "102", "10", 1_800_003_599_999],
        ])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        candles = await BinanceUsdmFuturesOHLCVAdapter(client).fetch_ohlcv(
            "btcusdt", "1h", limit=20
        )

    assert len(candles) == 2
    assert candles[0].open == Decimal("100")
    assert candles[1].close == Decimal("103")
    assert candles[0].timestamp < candles[1].timestamp


@pytest.mark.asyncio
async def test_bybit_linear_contract_klines_use_linear_category_and_sort_rows():
    def handler(request):
        assert request.url.path == "/v5/market/kline"
        assert request.url.params["category"] == "linear"
        assert request.url.params["interval"] == "60"
        return httpx.Response(200, json={
            "retCode": 0,
            "result": {"list": [
                ["1800003600000", "102", "104", "101", "103", "12.5", "1287.5"],
                ["1800000000000", "100", "103", "99", "102", "10", "1010"],
            ]},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        candles = await BybitLinearFuturesOHLCVAdapter(client).fetch_ohlcv(
            "BTCUSDT", "1h", limit=20
        )

    assert candles[0].open == Decimal("100")
    assert candles[1].close == Decimal("103")
    assert candles[0].timestamp < candles[1].timestamp


@pytest.mark.asyncio
async def test_futures_ohlcv_rejects_invalid_exchange_payload_and_interval():
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, json=[[1_800_000_000_000, "NaN", "103", "99", "102", "1"]]
    ))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(FuturesMarketDataError, match="invalid futures OHLCV"):
            await BinanceUsdmFuturesOHLCVAdapter(client).fetch_ohlcv("BTCUSDT", "1h")

    with pytest.raises(ValueError, match="interval"):
        await BybitLinearFuturesOHLCVAdapter().fetch_ohlcv("BTCUSDT", "2h")
