from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from app.auth import _session_token
from app.main import app
from app.market_data.futures import FuturesOverview
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
