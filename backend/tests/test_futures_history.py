from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.market_data.futures_history import (
    BinanceUsdmFuturesHistoryAdapter,
    BybitLinearFuturesHistoryAdapter,
)


@pytest.mark.asyncio
async def test_binance_history_adapter_normalizes_three_series():
    def handler(request):
        if request.url.path.endswith("/fundingRate"):
            return httpx.Response(200, json=[{
                "fundingTime": 1_800_000_000_000,
                "fundingRate": "0.0001",
                "markPrice": "100",
            }])
        if request.url.path.endswith("/openInterestHist"):
            return httpx.Response(200, json=[{
                "timestamp": 1_800_000_000_000,
                "sumOpenInterest": "12.5",
                "sumOpenInterestValue": "1250",
            }])
        return httpx.Response(200, json=[{
            "timestamp": 1_800_000_000_000,
            "longShortRatio": "1.5",
            "longAccount": "0.6",
            "shortAccount": "0.4",
        }])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await BinanceUsdmFuturesHistoryAdapter(client).fetch_history(
            "btcusdt", "1h", 50
        )

    assert result.symbol == "BTCUSDT"
    assert result.period == "1h"
    assert result.funding_rate[0].value == Decimal("0.0001")
    assert result.funding_rate[0].secondary_value == Decimal(100)
    assert result.open_interest[0].value == Decimal("12.5")
    assert result.open_interest[0].secondary_value == Decimal(1250)
    assert result.long_short_ratio[0].value == Decimal("1.5")


@pytest.mark.asyncio
async def test_bybit_history_adapter_normalizes_v5_series():
    def handler(request):
        path = request.url.path
        assert request.url.params["category"] == "linear"
        assert request.url.params["symbol"] == "ETHUSDT"
        if path.endswith("/funding/history"):
            rows = [{"fundingRateTimestamp": "1800000000000", "fundingRate": "-0.0002"}]
        elif path.endswith("/open-interest"):
            assert request.url.params["intervalTime"] == "1h"
            rows = [{"timestamp": "1800000000000", "openInterest": "42"}]
        else:
            assert request.url.params["period"] == "1h"
            rows = [{"timestamp": "1800000000000", "buyRatio": "0.6", "sellRatio": "0.4"}]
        return httpx.Response(200, json={"retCode": 0, "result": {"list": rows}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await BybitLinearFuturesHistoryAdapter(client).fetch_history(
            "ethusdt", "1h", 100
        )

    assert result.provider == "bybit_linear_futures"
    assert result.funding_rate[0].value == Decimal("-0.0002")
    assert result.open_interest[0].value == Decimal(42)
    assert result.long_short_ratio[0].value == Decimal("1.5")


@pytest.mark.asyncio
async def test_history_adapter_rejects_non_finite_values():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=[{
            "fundingTime": 1_800_000_000_000,
            "fundingRate": "NaN",
            "markPrice": "100",
        }] if request.url.path.endswith("/fundingRate") else [])
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(Exception, match="invalid derivatives history"):
            await BinanceUsdmFuturesHistoryAdapter(client).fetch_history(
                "BTCUSDT", "1h", 5
            )


@pytest.mark.asyncio
async def test_bybit_history_adapter_rejects_exchange_error():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"retCode": 10001, "retMsg": "invalid symbol"}
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        from app.market_data.futures import FuturesMarketDataError

        with pytest.raises(FuturesMarketDataError, match="unsuccessful"):
            await BybitLinearFuturesHistoryAdapter(client).fetch_history(
                "BTCUSDT", "1h", 5
            )


@pytest.mark.asyncio
async def test_binance_funding_history_paginates_forward():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    start_ms = int(start.timestamp() * 1000)
    end = start + timedelta(seconds=2000)
    calls = []

    def handler(request):
        calls.append(request)
        cursor = int(request.url.params["startTime"])
        if len(calls) == 1:
            assert cursor == start_ms
            rows = [
                {
                    "fundingTime": start_ms + index * 1000,
                    "fundingRate": "0.0001",
                    "markPrice": "100",
                }
                for index in range(1000)
            ]
        else:
            assert cursor == start_ms + 999_000 + 1
            rows = [{
                "fundingTime": start_ms + 1_000_000,
                "fundingRate": "-0.0001",
                "markPrice": "101",
            }]
        return httpx.Response(200, json=rows)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        points = await BinanceUsdmFuturesHistoryAdapter(client).fetch_funding_rates(
            "BTCUSDT", start, end
        )

    assert len(calls) == 2
    assert len(points) == 1001
    assert points[0].timestamp == start
    assert points[-1].value == Decimal("-0.0001")


@pytest.mark.asyncio
async def test_bybit_funding_history_paginates_backwards_to_start_time():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(seconds=1000)
    end_ms = int(end.timestamp() * 1000)
    calls = []

    def handler(request):
        calls.append(request)
        cursor = int(request.url.params["endTime"])
        assert "startTime" not in request.url.params
        if len(calls) == 1:
            assert cursor == end_ms
            rows = [
                {
                    "fundingRateTimestamp": str(end_ms - index * 1000),
                    "fundingRate": "0.0001",
                }
                for index in range(200)
            ]
        else:
            expected_cursor = end_ms - 199_000 - 1
            assert cursor == expected_cursor
            rows = [{
                "fundingRateTimestamp": str(expected_cursor - 1000),
                "fundingRate": "-0.0001",
            }]
        return httpx.Response(200, json={"retCode": 0, "result": {"list": rows}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        points = await BybitLinearFuturesHistoryAdapter(client).fetch_funding_rates(
            "ETHUSDT", start, end
        )

    assert len(calls) == 2
    assert len(points) == 201
    assert points == sorted(points, key=lambda point: point.timestamp)
    assert points[-1].timestamp == end
