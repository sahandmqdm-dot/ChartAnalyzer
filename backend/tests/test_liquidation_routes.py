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

    @property
    def state(self):
        return "stopped" if self.stopped or not self.started else "connected"

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
    client.cookies.set("ca_csrf", "test-csrf-token")
    client.headers.update({"X-CSRF-Token": "test-csrf-token"})
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

    now = datetime.now(UTC)

    async def aggregate(symbol, start_time, bucket_seconds, price_bins, provider, side, limit):
        assert symbol == "BTCUSDT"
        assert bucket_seconds == 300
        assert price_bins == 10
        assert provider is None
        assert side is None
        assert limit == 20000
        return {
            "total_events": 2,
            "total_notional": "32",
            "providers": ["binance_usdm_futures", "bybit_linear_futures"],
            "price_min": "100",
            "price_max": "110",
            "price_step": "1",
            "truncated": False,
            "cells": [
                {
                    "time_bucket": now.isoformat(),
                    "price_bin": 0,
                    "price_from": "100",
                    "price_to": "101",
                    "events": 1,
                    "long_events": 1,
                    "short_events": 0,
                    "long_notional": "10",
                    "short_notional": "0",
                    "total_notional": "10",
                },
                {
                    "time_bucket": now.isoformat(),
                    "price_bin": 9,
                    "price_from": "109",
                    "price_to": "110",
                    "events": 1,
                    "long_events": 0,
                    "short_events": 1,
                    "long_notional": "0",
                    "short_notional": "22",
                    "total_notional": "22",
                },
            ],
        }

    monkeypatch.setattr(liquidation_routes, "aggregate_liquidation_events", aggregate)
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

    monkeypatch.setattr(liquidation_routes, "aggregate_liquidation_events", no_database)
    response = authenticated_client().get(
        "/api/v1/derivatives/liquidations/heatmap?symbol=BTCUSDT"
    )
    assert response.status_code == 503


def test_liquidation_heatmap_validates_filters():
    response = authenticated_client().get(
        "/api/v1/derivatives/liquidations/heatmap?symbol=BTCUSDT&bucket_minutes=7"
    )
    assert response.status_code == 422


def test_liquidation_clusters_requires_authentication():
    response = TestClient(app).get(
        "/api/v1/derivatives/liquidations/clusters?symbol=BTCUSDT"
    )
    assert response.status_code == 401


def test_liquidation_clusters_groups_adjacent_strong_price_bands(monkeypatch):
    async def aggregate(symbol, start_time, price_bins, provider, side, limit):
        assert symbol == "BTCUSDT"
        assert price_bins == 40
        assert provider is None
        assert side is None
        assert limit == 20000
        return {
            "total_events": 8,
            "truncated": False,
            "price_min": "100",
            "price_max": "140",
            "price_step": "1",
            "total_notional": "500",
            "bands": [
                {"price_bin": 1, "price_from": "101", "price_to": "102", "events": 3,
                 "long_events": 3, "short_events": 0, "long_notional": "100",
                 "short_notional": "0", "total_notional": "100", "last_event_time": "2026-11-21T10:00:00+00:00"},
                {"price_bin": 2, "price_from": "102", "price_to": "103", "events": 2,
                 "long_events": 1, "short_events": 1, "long_notional": "80",
                 "short_notional": "20", "total_notional": "100", "last_event_time": "2026-11-21T10:01:00+00:00"},
                {"price_bin": 8, "price_from": "108", "price_to": "109", "events": 3,
                 "long_events": 0, "short_events": 3, "long_notional": "0",
                 "short_notional": "300", "total_notional": "300", "last_event_time": "2026-11-21T10:02:00+00:00"},
            ],
        }

    monkeypatch.setattr(liquidation_routes, "aggregate_liquidation_price_bands", aggregate)
    response = authenticated_client().get(
        "/api/v1/derivatives/liquidations/clusters?symbol=BTCUSDT&min_strength_pct=30&min_events=2"
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["clusters"]) == 2
    assert body["clusters"][0]["dominant_side"] == "short"
    assert body["clusters"][0]["events"] == 3
    assert body["clusters"][1]["price_bins"] == 2
    assert body["clusters"][1]["long_notional"] == "180"
    assert body["clusters"][1]["short_notional"] == "20"
    assert "not estimated open positions" in body["method"]


def test_liquidation_clusters_requires_database(monkeypatch):
    async def no_database(*_args, **_kwargs):
        return None

    monkeypatch.setattr(liquidation_routes, "aggregate_liquidation_price_bands", no_database)
    response = authenticated_client().get(
        "/api/v1/derivatives/liquidations/clusters?symbol=BTCUSDT"
    )
    assert response.status_code == 503


def test_liquidation_clusters_validates_parameters():
    response = authenticated_client().get(
        "/api/v1/derivatives/liquidations/clusters?symbol=BTCUSDT&min_strength_pct=0"
    )
    assert response.status_code == 422


def test_liquidation_start_requires_csrf():
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    response = client.post(
        "/api/v1/derivatives/liquidations/start?symbol=BTCUSDT"
    )
    assert response.status_code == 403


def test_liquidation_stop_requires_csrf():
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    response = client.delete(
        "/api/v1/derivatives/liquidations/stop?symbol=BTCUSDT"
    )
    assert response.status_code == 403


def test_liquidation_start_enforces_per_provider_capacity(monkeypatch):
    active = FakeCollector()
    active.start()
    candidate = FakeCollector()
    monkeypatch.setattr(settings, "max_active_liquidation_streams", 1)
    monkeypatch.setattr(
        liquidation_routes,
        "_collectors",
        {("bybit_linear_futures", "ETHUSDT"): active},
    )
    monkeypatch.setattr(liquidation_routes, "_collector_for", lambda _symbol: candidate)

    response = authenticated_client().post(
        "/api/v1/derivatives/liquidations/start?symbol=BTCUSDT"
    )

    assert response.status_code == 429
    assert candidate.started is False
