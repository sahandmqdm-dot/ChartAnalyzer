from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from app.auth import _session_token
from app.main import app
from app.market_data.futures_history import FuturesHistory, FuturesSeriesPoint
from app.settings import settings


class FakeHistoryAdapter:
    name = "binance_usdm_futures"

    def __init__(self):
        pass

    async def fetch_history(self, symbol: str, period: str, limit: int) -> FuturesHistory:
        point = FuturesSeriesPoint(
            timestamp=datetime(2026, 11, 21, tzinfo=UTC),
            value=Decimal("0.001"),
        )
        return FuturesHistory(
            provider="binance_usdm_futures",
            market_type="linear_perpetual",
            symbol=symbol,
            period=period,
            funding_rate=[point],
            open_interest=[point],
            long_short_ratio=[point],
            observed_at=datetime(2026, 11, 21, tzinfo=UTC),
        )


def test_futures_history_requires_authentication():
    response = TestClient(app).get(
        "/api/v1/derivatives/history", params={"symbol": "BTCUSDT"}
    )
    assert response.status_code == 401


def test_futures_history_returns_normalized_series(monkeypatch):
    from app.market_data import futures_history_routes

    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "market_data_provider", "binance_spot")
    monkeypatch.setattr(
        futures_history_routes, "BinanceUsdmFuturesHistoryAdapter", FakeHistoryAdapter
    )
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    response = client.get(
        "/api/v1/derivatives/history",
        params={"symbol": "btcusdt", "period": "1h", "limit": 20},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == "BTCUSDT"
    assert body["period"] == "1h"
    assert body["funding_rate"][0]["value"] == "0.001"


def test_futures_history_rejects_unsupported_period(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    response = client.get(
        "/api/v1/derivatives/history",
        params={"symbol": "BTCUSDT", "period": "2m"},
    )
    assert response.status_code == 422
