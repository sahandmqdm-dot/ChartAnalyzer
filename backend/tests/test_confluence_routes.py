from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import ClassVar

from fastapi.testclient import TestClient

from app.auth import _session_token
from app.main import app
from app.market_data import confluence_routes
from app.market_data.mark_price import MarkPriceCandle, MarkPriceHistory
from app.settings import settings


class FakeProvider:
    name = "binance_usdm_futures"


class FakeAdapter:
    name = "binance_usdm_futures"
    periods: ClassVar[list[str]] = []

    async def fetch_mark_price_history(self, symbol, period, limit):
        self.periods.append(period)
        candles = []
        duration = {"5m": 5, "15m": 15, "1h": 60, "4h": 240}[period]
        for index in range(30):
            stamp = datetime(2026, 11, 20, tzinfo=UTC) + timedelta(minutes=index * duration)
            base = Decimal(100 + (index % 7))
            candles.append(MarkPriceCandle(
                timestamp=stamp,
                close_time=stamp + timedelta(minutes=duration) - timedelta(milliseconds=1),
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


def test_confluence_route_requires_authentication():
    response = TestClient(app).get(
        "/api/v1/derivatives/confluence", params={"symbol": "BTCUSDT"}
    )
    assert response.status_code == 401


def test_confluence_route_fetches_all_four_timeframes(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(confluence_routes, "get_futures_provider", FakeProvider)
    monkeypatch.setattr(confluence_routes, "BinanceUsdmMarkPriceAdapter", FakeAdapter)
    FakeAdapter.periods = []
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    response = client.get(
        "/api/v1/derivatives/confluence",
        params={"symbol": "ETHUSDT", "limit": 30, "pivot_window": 2},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == "ETHUSDT"
    assert body["provider"] == "binance_usdm_futures"
    assert {item["period"] for item in body["timeframes"]} == {"5m", "15m", "1h", "4h"}
    assert sorted(FakeAdapter.periods) == ["15m", "1h", "4h", "5m"]
    assert body["unavailable_timeframes"] == []


def test_confluence_route_rejects_invalid_symbol(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    response = client.get(
        "/api/v1/derivatives/confluence", params={"symbol": "BTC/USDT"}
    )
    assert response.status_code == 422
