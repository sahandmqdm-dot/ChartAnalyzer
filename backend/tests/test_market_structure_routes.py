from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from app.auth import _session_token
from app.main import app
from app.market_data import market_structure_routes
from app.market_data.mark_price import MarkPriceCandle, MarkPriceHistory
from app.settings import settings


class FakeProvider:
    name = "binance_usdm_futures"


class FakeAdapter:
    name = "binance_usdm_futures"

    async def fetch_mark_price_history(self, symbol, period, limit):
        candles = []
        for index in range(30):
            stamp = datetime(2026, 11, 20, tzinfo=UTC) + timedelta(hours=index)
            base = Decimal(100 + (index % 7))
            candles.append(MarkPriceCandle(
                timestamp=stamp,
                close_time=stamp.replace(minute=59, second=59),
                is_closed=True,
                open=base,
                high=base + Decimal(2),
                low=base - Decimal(2),
                close=base + Decimal(1),
            ))
        return MarkPriceHistory(
            provider=self.name, market_type="linear_perpetual", symbol=symbol,
            period=period, candles=candles, observed_at=datetime(2026, 11, 21, tzinfo=UTC),
        )


def test_market_structure_route_requires_authentication():
    response = TestClient(app).get(
        "/api/v1/derivatives/market-structure", params={"symbol": "BTCUSDT"}
    )
    assert response.status_code == 401


def test_market_structure_route_returns_closed_candle_analysis(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(market_structure_routes, "get_futures_provider", FakeProvider)
    monkeypatch.setattr(market_structure_routes, "BinanceUsdmMarkPriceAdapter", FakeAdapter)
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    response = client.get(
        "/api/v1/derivatives/market-structure",
        params={"symbol": "btcusdt", "period": "1h", "limit": 30, "pivot_window": 2},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == "BTCUSDT"
    assert body["closed_candles"] == 30
    assert body["provider"] == "binance_usdm_futures"


def test_market_structure_route_rejects_bad_period(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(market_structure_routes, "get_futures_provider", FakeProvider)
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    response = client.get(
        "/api/v1/derivatives/market-structure",
        params={"symbol": "BTCUSDT", "period": "2m"},
    )
    assert response.status_code == 422
