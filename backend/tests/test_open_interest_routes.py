from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.auth import _session_token
from app.main import app
from app.settings import settings


def client_with_auth(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    return client


def test_live_open_interest_requires_auth():
    response = TestClient(app).get(
        "/api/v1/derivatives/open-interest-live", params={"symbol": "BTCUSDT"}
    )
    assert response.status_code == 401


def test_live_open_interest_starts_collector(monkeypatch):
    class FakeCollector:
        started = False

        def start(self):
            self.started = True

        def snapshot(self):
            return {"symbol": "BTCUSDT", "state": "connecting"}

    collector = FakeCollector()
    monkeypatch.setattr(
        "app.market_data.open_interest.get_open_interest_collector",
        lambda _symbol: collector,
    )
    response = client_with_auth(monkeypatch).get(
        "/api/v1/derivatives/open-interest-live", params={"symbol": "btcusdt"}
    )
    assert response.status_code == 200
    assert collector.started
    assert response.json()["symbol"] == "BTCUSDT"


def test_open_interest_history_returns_observations(monkeypatch):
    async def fake_list(symbol, start_time, limit, provider):
        assert symbol == "BTCUSDT"
        assert provider == "binance_usdm_futures"
        assert limit == 25
        assert start_time.tzinfo is not None
        return [{
            "provider": provider,
            "symbol": symbol,
            "observed_at": datetime(2026, 11, 22, tzinfo=UTC).isoformat(),
            "open_interest": "12345.6",
        }]

    monkeypatch.setattr(
        "app.market_data.open_interest_repository.list_open_interest", fake_list
    )
    response = client_with_auth(monkeypatch).get(
        "/api/v1/derivatives/open-interest-history",
        params={"symbol": "BTCUSDT", "hours": 12, "limit": 25},
    )
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1
    assert response.json()["observations"][0]["open_interest"] == "12345.6"


def test_open_interest_history_returns_503_without_database(monkeypatch):
    async def disabled(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        "app.market_data.open_interest_repository.list_open_interest", disabled
    )
    response = client_with_auth(monkeypatch).get(
        "/api/v1/derivatives/open-interest-history", params={"symbol": "BTCUSDT"}
    )
    assert response.status_code == 503
