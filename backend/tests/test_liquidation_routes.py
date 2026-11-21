from fastapi.testclient import TestClient

from app.auth import _session_token
from app.main import app
from app.market_data import liquidation_routes
from app.settings import settings


class FakeCollector:
    def __init__(self):
        self.provider = "bybit_linear_futures"
        self.symbol = "BTCUSDT"
        self.started = False
        self.stopped = False
        self.events = []
        self._seen = set()

    def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    def clear(self):
        self.events.clear()
        self._seen.clear()

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



def test_snapshot_prefers_durable_database_events(monkeypatch):
    from datetime import UTC, datetime
    from decimal import Decimal

    from app.market_data.liquidation_stream import LiquidationEvent

    collector = FakeCollector()
    monkeypatch.setattr(liquidation_routes, "_collector_for", lambda symbol: collector)

    async def durable_events(provider, symbol, limit):
        assert provider == "bybit_linear_futures"
        assert symbol == "BTCUSDT"
        assert limit == 5
        return [
            LiquidationEvent(
                event_id="c" * 24,
                provider=provider,
                symbol=symbol,
                event_time=datetime(2026, 11, 21, tzinfo=UTC),
                liquidation_side="long",
                order_side="BUY",
                quantity=Decimal(1),
                price=Decimal(100),
                notional=Decimal(100),
                price_kind="bankruptcy",
            )
        ]

    monkeypatch.setattr(liquidation_routes, "list_recent_liquidation_events", durable_events)
    response = authenticated_client().get(
        "/api/v1/derivatives/liquidations?symbol=BTCUSDT&limit=5"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["storage_status"] == "available"
    assert body["retention"] == "postgresql_persistent"
    assert body["events"][0]["event_id"] == "c" * 24


def test_liquidation_heatmap_requires_authentication():
    response = TestClient(app).get(
        "/api/v1/derivatives/liquidations/heatmap?symbol=BTCUSDT"
    )
    assert response.status_code == 401


def test_liquidation_heatmap_aggregates_observed_events(monkeypatch):
    from datetime import UTC, datetime
    from decimal import Decimal

    from app.market_data.liquidation_stream import LiquidationEvent

    now = datetime.now(UTC)
    events = [
        LiquidationEvent(
            event_id="d" * 24,
            provider="binance_usdm_futures",
            symbol="BTCUSDT",
            event_time=now,
            liquidation_side="long",
            order_side="SELL",
            quantity=Decimal("0.1"),
            price=Decimal("100"),
            notional=Decimal("10"),
            price_kind="average_execution",
        ),
        LiquidationEvent(
            event_id="e" * 24,
            provider="bybit_linear_futures",
            symbol="BTCUSDT",
            event_time=now,
            liquidation_side="short",
            order_side="BUY",
            quantity=Decimal("0.2"),
            price=Decimal("110"),
            notional=Decimal("22"),
            price_kind="bankruptcy",
        ),
    ]

    async def events_since(symbol, start_time, provider, limit):
        assert symbol == "BTCUSDT"
        assert provider is None
        assert limit == 20001
        return events

    monkeypatch.setattr(liquidation_routes, "list_liquidation_events_since", events_since)
    response = authenticated_client().get(
        "/api/v1/derivatives/liquidations/heatmap?symbol=BTCUSDT&price_bins=10"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total_events"] == 2
    assert body["total_notional"] == "32"
    assert set(body["providers"]) == {"binance_usdm_futures", "bybit_linear_futures"}
    assert sum(cell["long_events"] for cell in body["cells"]) == 1
    assert sum(cell["short_events"] for cell in body["cells"]) == 1
    assert "not estimated future liquidation levels" in body["data_semantics"]


def test_liquidation_heatmap_reports_missing_database(monkeypatch):
    async def no_database(*_args, **_kwargs):
        return None

    monkeypatch.setattr(liquidation_routes, "list_liquidation_events_since", no_database)
    response = authenticated_client().get(
        "/api/v1/derivatives/liquidations/heatmap?symbol=BTCUSDT"
    )
    assert response.status_code == 503


def test_liquidation_heatmap_validates_filters():
    response = authenticated_client().get(
        "/api/v1/derivatives/liquidations/heatmap?symbol=BTCUSDT&bucket_minutes=7"
    )
    assert response.status_code == 422
