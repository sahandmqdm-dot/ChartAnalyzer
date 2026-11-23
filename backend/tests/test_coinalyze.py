from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from app.market_data.coinalyze import (
    CoinalyzeClient,
    CoinalyzeError,
    normalize_symbols,
)


@pytest.mark.parametrize(
    ("symbols", "expected"),
    [
        (["btcusdt.6"], ["BTCUSDT.6"]),
        (["BTCUSDT_PERP.A", "BTC.H"], ["BTCUSDT_PERP.A", "BTC.H"]),
    ],
)
def test_normalize_symbols(symbols, expected):
    assert normalize_symbols(symbols) == expected


@pytest.mark.parametrize(
    "symbols",
    [
        [],
        ["BTC/USDT"],
        ["BTCUSDT.6", "btcusdt.6"],
        [f"BTC{i}.A" for i in range(21)],
    ],
)
def test_normalize_symbols_rejects_invalid_input(symbols):
    with pytest.raises(ValueError):
        normalize_symbols(symbols)


@pytest.mark.asyncio
async def test_fetch_snapshot_uses_api_key_and_converts_milliseconds_to_utc():
    timestamp_ms = 1795452850546

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["api_key"] == "test-coinalyze-key"
        if request.url.path.endswith("/open-interest"):
            rows = [{"symbol": "BTC.H", "value": 37728.76524, "update": timestamp_ms}]
        else:
            rows = [{"symbol": "BTC.H", "value": 0.00021097, "update": timestamp_ms}]
        return httpx.Response(200, json=rows)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await CoinalyzeClient("test-coinalyze-key", client=http).fetch_snapshot(
            ["btc.h"]
        )

    assert len(result) == 1
    assert result[0].symbol == "BTC.H"
    assert result[0].open_interest == Decimal("37728.76524")
    assert result[0].funding_rate == Decimal("0.00021097")
    assert result[0].open_interest_updated_at == datetime.fromtimestamp(
        timestamp_ms / 1000, tz=UTC
    )


@pytest.mark.asyncio
async def test_fetch_snapshot_maps_rate_limit_without_leaking_response_body():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="private upstream detail")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = CoinalyzeClient("test-coinalyze-key", client=http)
        with pytest.raises(CoinalyzeError, match="rate limit"):
            await client.fetch_snapshot(["BTC.H"])


@pytest.mark.asyncio
async def test_fetch_snapshot_rejects_non_finite_values():
    def handler(request: httpx.Request) -> httpx.Response:
        rows = [{"symbol": "BTC.H", "value": "NaN", "update": 1795452850546}]
        return httpx.Response(200, json=rows)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(CoinalyzeError, match="non-finite"):
            await CoinalyzeClient("test-coinalyze-key", client=http).fetch_snapshot(
                ["BTC.H"]
            )
