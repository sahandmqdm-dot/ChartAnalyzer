from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from app.auth import _session_token
from app.main import app
from app.market_data.futures import (
    BinanceUsdmFuturesAdapter,
    BybitLinearFuturesAdapter,
    FuturesOverview,
)
from app.market_data.futures_routes import get_futures_provider
from app.settings import settings


class FakeFuturesProvider:
    name = "fake_linear_futures"

    async def fetch_overview(self, symbol: str) -> FuturesOverview:
        return FuturesOverview(
            provider=self.name,
            market_type="linear_perpetual",
            symbol=symbol,
            mark_price=Decimal(100),
            index_price=Decimal("99.9"),
            funding_rate=Decimal("0.0001"),
            open_interest=Decimal(20),
            open_interest_value=Decimal(2000),
            next_funding_time=datetime(2026, 11, 22, 0, 0, tzinfo=UTC),
            observed_at=datetime(2026, 11, 21, 12, 0, tzinfo=UTC),
        )


def test_futures_overview_requires_authentication():
    response = TestClient(app).get(
        "/api/v1/derivatives/overview", params={"symbol": "BTCUSDT"}
    )
    assert response.status_code == 401


def test_futures_overview_returns_normalized_authenticated_data(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    app.dependency_overrides[get_futures_provider] = FakeFuturesProvider
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    try:
        response = client.get(
            "/api/v1/derivatives/overview", params={"symbol": "btcusdt"}
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "fake_linear_futures"
    assert body["market_type"] == "linear_perpetual"
    assert body["symbol"] == "BTCUSDT"
    assert body["mark_price"] == "100"
    assert body["funding_rate"] == "0.0001"
    assert body["open_interest_value"] == "2000"


def test_futures_overview_rejects_invalid_symbol(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    app.dependency_overrides[get_futures_provider] = FakeFuturesProvider
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    try:
        response = client.get(
            "/api/v1/derivatives/overview", params={"symbol": "BTC/USDT"}
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 422


def risk_payload():
    return {
        "symbol": "btcusdt",
        "side": "long",
        "entry_price": "100",
        "mark_price": "95",
        "quantity": "9",
        "leverage": "10",
        "estimated_close_fee_rate": "0.0005",
        "risk_tiers": [
            {
                "tier": 1, "notional_floor": "0", "notional_cap": "1000",
                "maintenance_margin_rate": "0.02",
                "maintenance_margin_deduction": "0", "max_leverage": "50",
            },
            {
                "tier": 2, "notional_floor": "1000", "notional_cap": None,
                "maintenance_margin_rate": "0.05",
                "maintenance_margin_deduction": "30", "max_leverage": "20",
            },
        ],
    }


def test_isolated_risk_estimate_requires_authentication():
    response = TestClient(app).post(
        "/api/v1/derivatives/risk/isolated-liquidation-estimate",
        json=risk_payload(),
    )
    assert response.status_code == 401


def test_isolated_risk_estimate_returns_bankruptcy_and_liquidation_separately(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    response = client.post(
        "/api/v1/derivatives/risk/isolated-liquidation-estimate",
        json=risk_payload(),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == "BTCUSDT"
    assert body["status"] == "estimate_only"
    assert Decimal(body["bankruptcy_price_estimate"]) == Decimal(90)
    assert Decimal(body["liquidation_price_estimate"]) > Decimal(
        body["bankruptcy_price_estimate"]
    )
    assert Decimal(body["distance_to_liquidation_from_mark"]) > 0
    assert Decimal(body["distance_to_liquidation_from_mark_percent"]) > 0


def test_futures_provider_defaults_to_binance_independently_of_spot(monkeypatch):
    monkeypatch.setattr(settings, "market_data_provider", "bybit_spot")
    monkeypatch.setattr(settings, "futures_market_data_provider", "binance_usdm_futures")
    provider = get_futures_provider()
    assert isinstance(provider, BinanceUsdmFuturesAdapter)
    assert provider.name == "binance_usdm_futures"


def test_futures_provider_can_select_bybit_while_spot_uses_binance(monkeypatch):
    monkeypatch.setattr(settings, "market_data_provider", "binance_spot")
    monkeypatch.setattr(settings, "futures_market_data_provider", "bybit_linear_futures")
    provider = get_futures_provider()
    assert isinstance(provider, BybitLinearFuturesAdapter)
    assert provider.name == "bybit_linear_futures"
