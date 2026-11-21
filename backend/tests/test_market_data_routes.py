from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from app.auth import _session_token
from app.main import app
from app.market_data.candle import Candle
from app.market_data.registry import get_market_data_provider
from app.settings import settings


class FakeHistoricalProvider:
    name = "fake"

    def __init__(self):
        self.arguments = None

    async def fetch_ohlcv(self, symbol, interval="1h", **kwargs):
        raise AssertionError("standard candle endpoint not expected")

    async def fetch_historical_ohlcv(
        self,
        symbol,
        interval="1h",
        *,
        start_time,
        end_time,
        page_limit=1000,
        max_candles=10_000,
    ):
        self.arguments = {
            "symbol": symbol,
            "interval": interval,
            "start_time": start_time,
            "end_time": end_time,
            "page_limit": page_limit,
            "max_candles": max_candles,
        }
        return [
            Candle(
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                close_time=datetime(2026, 1, 1, 0, 59, 59, 999000, tzinfo=UTC),
                is_closed=True,
                open=Decimal(10),
                high=Decimal(12),
                low=Decimal(9),
                close=Decimal(11),
                volume=Decimal(5),
            ),
            Candle(
                timestamp=datetime(2026, 1, 1, 1, tzinfo=UTC),
                close_time=datetime(2026, 1, 1, 1, 59, 59, 999000, tzinfo=UTC),
                is_closed=False,
                open=Decimal(11),
                high=Decimal(13),
                low=Decimal(10),
                close=Decimal(12),
                volume=Decimal(6),
            ),
        ]


def test_history_endpoint_requires_authentication():
    client = TestClient(app)
    response = client.get(
        "/api/v1/market-data/candles/history",
        params={
            "symbol": "BTCUSDT",
            "start_time": "2026-01-01T00:00:00Z",
            "end_time": "2026-01-02T00:00:00Z",
        },
    )
    assert response.status_code == 401


def test_history_endpoint_returns_bounded_historical_candles(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    provider = FakeHistoricalProvider()
    app.dependency_overrides[get_market_data_provider] = lambda: provider
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    try:
        response = client.get(
            "/api/v1/market-data/candles/history",
            params={
                "symbol": "btcusdt",
                "interval": "1h",
                "start_time": "2026-01-01T00:00:00Z",
                "end_time": "2026-01-02T00:00:00Z",
                "page_limit": 250,
                "max_candles": 5000,
                "closed_only": "true",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "fake"
    assert payload["symbol"] == "BTCUSDT"
    assert payload["count"] == 1
    assert len(payload["candles"]) == 1
    assert payload["candles"][0]["is_closed"] is True
    assert provider.arguments["page_limit"] == 250
    assert provider.arguments["max_candles"] == 5000


def test_history_endpoint_rejects_unbounded_parameters(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    response = client.get(
        "/api/v1/market-data/candles/history",
        params={
            "symbol": "BTCUSDT",
            "start_time": "2026-01-01T00:00:00Z",
            "end_time": "2026-01-02T00:00:00Z",
            "max_candles": 10001,
        },
    )
    assert response.status_code == 422


class FakeNamedProvider:
    def __init__(self, name: str, close: str):
        self.name = name
        self.close = Decimal(close)

    async def fetch_ohlcv(self, symbol, interval="1h", **kwargs):
        return [
            Candle(
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                close_time=datetime(2026, 1, 1, 0, 59, 59, 999000, tzinfo=UTC),
                is_closed=True,
                open=Decimal(10),
                high=Decimal(12),
                low=Decimal(9),
                close=self.close,
                volume=Decimal(5),
            )
        ]

    async def fetch_historical_ohlcv(self, *args, **kwargs):
        raise AssertionError("historical endpoint not expected")


def test_candle_cache_isolated_by_provider(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    provider_a = FakeNamedProvider("provider-a", "11")
    provider_b = FakeNamedProvider("provider-b", "22")

    try:
        app.dependency_overrides[get_market_data_provider] = lambda: provider_a
        response_a = client.get(
            "/api/v1/market-data/candles",
            params={"symbol": "ETHUSDT", "interval": "1h"},
        )
        app.dependency_overrides[get_market_data_provider] = lambda: provider_b
        response_b = client.get(
            "/api/v1/market-data/candles",
            params={"symbol": "ETHUSDT", "interval": "1h"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response_a.status_code == 200
    assert response_b.status_code == 200
    assert response_a.json()["candles"][0]["close"] == "11"
    assert response_b.json()["candles"][0]["close"] == "22"
