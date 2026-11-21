from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from app.auth import _session_token
from app.main import app
from app.market_data import mark_price_routes
from app.market_data.mark_price import MarkPriceCandle, MarkPriceHistory
from app.settings import settings


class FakeConfiguredProvider:
    name = "binance_usdm_futures"


class FakeMarkPriceAdapter:
    name = "binance_usdm_futures"

    async def fetch_mark_price_history(self, symbol, period, limit):
        return MarkPriceHistory(
            provider=self.name,
            market_type="linear_perpetual",
            symbol=symbol,
            period=period,
            candles=[MarkPriceCandle(
                timestamp=datetime(2026, 11, 21, 12, 0, tzinfo=UTC),
                close_time=datetime(2026, 11, 21, 12, 59, 59, 999000, tzinfo=UTC),
                is_closed=True,
                open=Decimal(100),
                high=Decimal(105),
                low=Decimal(99),
                close=Decimal(103),
            )],
            observed_at=datetime(2026, 11, 21, 13, 0, tzinfo=UTC),
        )


def test_mark_price_history_requires_authentication():
    response = TestClient(app).get(
        "/api/v1/derivatives/mark-price-history", params={"symbol": "BTCUSDT"}
    )
    assert response.status_code == 401


def test_mark_price_history_returns_normalized_data(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(mark_price_routes, "get_futures_provider", FakeConfiguredProvider)
    monkeypatch.setattr(mark_price_routes, "BinanceUsdmMarkPriceAdapter", FakeMarkPriceAdapter)
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    response = client.get(
        "/api/v1/derivatives/mark-price-history",
        params={"symbol": "btcusdt", "period": "1h", "limit": 10},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "binance_usdm_futures"
    assert body["candles"][0]["open"] == "100"
    assert body["candles"][0]["close"] == "103"


def test_mark_price_history_rejects_invalid_period(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(mark_price_routes, "get_futures_provider", FakeConfiguredProvider)
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    response = client.get(
        "/api/v1/derivatives/mark-price-history",
        params={"symbol": "BTCUSDT", "period": "2m"},
    )
    assert response.status_code == 422
