import httpx
import pytest

from app.market_data.futures import FuturesMarketDataError
from app.market_data.risk_tiers import BybitLinearRiskTierAdapter


@pytest.mark.asyncio
async def test_bybit_risk_tiers_are_normalized_with_cumulative_deduction():
    def handler(request):
        assert request.url.path.endswith("/v5/market/risk-limit")
        assert request.url.params["category"] == "linear"
        assert request.url.params["symbol"] == "BTCUSDT"
        return httpx.Response(200, json={
            "retCode": 0,
            "result": {"list": [
                {
                    "id": 2, "symbol": "BTCUSDT", "riskLimitValue": "200000",
                    "maintenanceMargin": "0.005", "initialMargin": "0.01",
                    "maxLeverage": "100", "mmDeduction": "500",
                },
                {
                    "id": 1, "symbol": "BTCUSDT", "riskLimitValue": "100000",
                    "maintenanceMargin": "0.004", "initialMargin": "0.01",
                    "maxLeverage": "125", "mmDeduction": "0",
                },
            ]},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await BybitLinearRiskTierAdapter(client).fetch_risk_tiers("btcusdt")

    assert result.symbol == "BTCUSDT"
    assert result.tiers[0].notional_floor == 0
    assert result.tiers[0].notional_cap == 100000
    assert result.tiers[1].notional_floor == 100000
    assert result.tiers[1].notional_cap is None
    assert result.tiers[1].maintenance_margin_deduction == 500


@pytest.mark.asyncio
async def test_bybit_risk_tiers_reject_exchange_error():
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, json={"retCode": 10001, "retMsg": "invalid symbol"}
    ))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(FuturesMarketDataError, match="unsuccessful"):
            await BybitLinearRiskTierAdapter(client).fetch_risk_tiers("BTCUSDT")


@pytest.mark.asyncio
async def test_bybit_risk_tiers_rejects_missing_symbol_rows():
    transport = httpx.MockTransport(lambda request: httpx.Response(
        200, json={"retCode": 0, "result": {"list": []}}
    ))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(FuturesMarketDataError, match="no risk tiers"):
            await BybitLinearRiskTierAdapter(client).fetch_risk_tiers("BTCUSDT")
