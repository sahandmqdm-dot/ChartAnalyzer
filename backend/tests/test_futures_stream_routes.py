from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.auth import _session_token
from app.main import app
from app.market_data import futures_stream
from app.settings import settings


def authenticated_client(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    return client


def test_live_market_endpoint_requires_authentication():
    response = TestClient(app).get(
        "/api/v1/derivatives/live-market", params={"symbol": "BTCUSDT"}
    )
    assert response.status_code == 401


def test_live_market_endpoint_starts_stream_and_returns_snapshot(monkeypatch):
    class FakeStream:
        started = False

        def start(self):
            self.started = True

        def snapshot(self):
            return {"symbol": "BTCUSDT", "state": "connecting"}

    stream = FakeStream()
    monkeypatch.setattr(futures_stream, "get_futures_market_stream", lambda _symbol: stream)
    response = authenticated_client(monkeypatch).get(
        "/api/v1/derivatives/live-market", params={"symbol": "btcusdt"}
    )
    assert response.status_code == 200
    assert stream.started
    assert response.json()["symbol"] == "BTCUSDT"


def test_market_history_endpoint_returns_persisted_rows(monkeypatch):
    async def fake_list(symbol, start_time, limit, provider):
        assert symbol == "BTCUSDT"
        assert provider == "binance_usdm_futures"
        assert limit == 50
        assert start_time.tzinfo is not None
        return [{
            "provider": provider,
            "symbol": symbol,
            "observed_at": datetime(2026, 11, 22, tzinfo=UTC).isoformat(),
            "mark_price": "91000",
            "index_price": "90999",
            "funding_rate": "0.0001",
            "next_funding_time": datetime(2026, 11, 22, 4, tzinfo=UTC).isoformat(),
        }]

    monkeypatch.setattr(
        "app.market_data.futures_snapshot_repository.list_futures_mark_snapshots",
        fake_list,
    )
    response = authenticated_client(monkeypatch).get(
        "/api/v1/derivatives/market-history",
        params={"symbol": "btcusdt", "hours": 12, "limit": 50},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 1
    assert body["snapshots"][0]["mark_price"] == "91000"


def test_market_history_returns_service_unavailable_without_database(monkeypatch):
    async def disabled(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "app.market_data.futures_snapshot_repository.list_futures_mark_snapshots",
        disabled,
    )
    response = authenticated_client(monkeypatch).get(
        "/api/v1/derivatives/market-history", params={"symbol": "BTCUSDT"}
    )
    assert response.status_code == 503
