from decimal import Decimal

from fastapi.testclient import TestClient

from app.auth import _session_token
from app.main import app
from app.market_data import futures_risk_routes
from app.settings import settings


class FakeBybitProvider:
    name = "bybit_linear_futures"


class FakeRiskTierAdapter:
    async def fetch_risk_tiers(self, symbol):
        from app.market_data.risk_tiers import RiskTierSet
        from app.market_data.futures_risk import RiskTier

        return RiskTierSet(
            provider="bybit_linear_futures",
            category="linear",
            symbol=symbol,
            tiers=[
                RiskTier(
                    tier=1, notional_floor=Decimal(0), notional_cap=Decimal(100000),
                    maintenance_margin_rate=Decimal("0.004"),
                    maintenance_margin_deduction=Decimal(0),
                    max_leverage=Decimal(125),
                ),
                RiskTier(
                    tier=2, notional_floor=Decimal(100000), notional_cap=None,
                    maintenance_margin_rate=Decimal("0.005"),
                    maintenance_margin_deduction=Decimal(500),
                    max_leverage=Decimal(100),
                ),
            ],
        )


def authenticated_client():
    settings.admin_username = "admin"
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    return client


def test_risk_estimate_requires_authentication():
    response = TestClient(app).post("/api/v1/derivatives/risk-estimate", json={})
    assert response.status_code == 401


def test_risk_estimate_returns_tier_aware_isolated_result():
    response = authenticated_client().post(
        "/api/v1/derivatives/risk-estimate",
        json={
            "symbol": "btcusdt",
            "side": "long",
            "entry_price": "100",
            "quantity": "1",
            "leverage": "10",
            "risk_tiers": [
                {
                    "tier": 1, "notional_floor": "0", "notional_cap": None,
                    "maintenance_margin_rate": "0.005",
                    "maintenance_margin_deduction": "0",
                    "max_leverage": "50",
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == "BTCUSDT"
    assert body["margin_mode"] == "isolated"
    assert Decimal(body["liquidation_price_estimate"]) < Decimal(100)
    assert body["status"] == "estimate_only"


def test_risk_estimate_rejects_invalid_tier_payload():
    response = authenticated_client().post(
        "/api/v1/derivatives/risk-estimate",
        json={
            "symbol": "BTCUSDT", "side": "long",
            "entry_price": "100", "quantity": "1", "leverage": "10",
            "risk_tiers": [
                {
                    "tier": 1, "notional_floor": "1", "notional_cap": None,
                    "maintenance_margin_rate": "0.005",
                    "maintenance_margin_deduction": "0",
                }
            ],
        },
    )
    assert response.status_code == 422


def test_public_risk_tier_endpoint_returns_bybit_tiers(monkeypatch):
    monkeypatch.setattr(futures_risk_routes, "get_futures_provider", FakeBybitProvider)
    monkeypatch.setattr(
        futures_risk_routes, "BybitLinearRiskTierAdapter", FakeRiskTierAdapter
    )
    response = authenticated_client().get(
        "/api/v1/derivatives/risk-tiers", params={"symbol": "BTCUSDT"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "bybit_linear_futures"
    assert body["tiers"][1]["maintenance_margin_deduction"] == "500"


def test_binance_risk_tiers_are_not_falsely_claimed_public(monkeypatch):
    class FakeBinanceProvider:
        name = "binance_usdm_futures"

    monkeypatch.setattr(futures_risk_routes, "get_futures_provider", FakeBinanceProvider)
    response = authenticated_client().get(
        "/api/v1/derivatives/risk-tiers", params={"symbol": "BTCUSDT"}
    )
    assert response.status_code == 501
