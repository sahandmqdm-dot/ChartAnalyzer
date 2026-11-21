from fastapi.testclient import TestClient

from app.auth import _session_token
from app.main import app
from app.market_data import liquidation_routes
from app.settings import settings


class FakeCollector:
    def __init__(self):
        self.started = False
        self.stopped = False
        self.events = []
        self._seen = set()

    def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    def snapshot(self, limit=100):
        return {
            "status": {
                "provider": "bybit_linear_futures",
                "symbol": "BTCUSDT",
                "state": "connected" if self.started and not self.stopped else "stopped",
                "event_count": len(self.events),
                "last_event_time": None,
                "last_error": None,
            },
            "events": self.events[:limit],
            "retention": "in_memory_since_collector_start",
        }


def authenticated_client():
    settings.admin_username = "admin"
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    return client


def test_liquidation_endpoints_require_authentication():
    client = TestClient(app)
    assert client.get("/api/v1/derivatives/liquidations?symbol=BTCUSDT").status_code == 401
    assert client.post("/api/v1/derivatives/liquidations/start?symbol=BTCUSDT").status_code == 401
    assert client.delete("/api/v1/derivatives/liquidations/stop?symbol=BTCUSDT").status_code == 401


def test_start_and_snapshot_liquidation_collector(monkeypatch):
    collector = FakeCollector()
    monkeypatch.setattr(liquidation_routes, "_collector_for", lambda symbol: collector)
    client = authenticated_client()

    started = client.post("/api/v1/derivatives/liquidations/start?symbol=btcusdt")
    assert started.status_code == 200
    assert started.json()["status"]["state"] == "connected"
    assert collector.started is True

    snapshot = client.get("/api/v1/derivatives/liquidations?symbol=BTCUSDT&limit=10")
    assert snapshot.status_code == 200
    assert snapshot.json()["retention"] == "in_memory_since_collector_start"


def test_stop_liquidation_collector(monkeypatch):
    collector = FakeCollector()
    collector.start()
    monkeypatch.setattr(liquidation_routes, "_collector_for", lambda symbol: collector)
    response = authenticated_client().delete(
        "/api/v1/derivatives/liquidations/stop?symbol=BTCUSDT"
    )
    assert response.status_code == 200
    assert response.json()["status"]["state"] == "stopped"
    assert collector.stopped is True
