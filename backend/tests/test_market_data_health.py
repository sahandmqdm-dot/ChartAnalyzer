from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.auth import _session_token
from app.main import app
from app.market_data.health import build_market_data_health
from app.market_data import health_routes
from app.settings import settings


class FakeFeed:
    def __init__(self, provider, snapshot, interval_seconds=None):
        self.provider = provider
        self._snapshot = snapshot
        if interval_seconds is not None:
            self.interval_seconds = interval_seconds

    def snapshot(self):
        return self._snapshot


def _snapshot(kind, now, *, state="connected", persistence_state="available"):
    return {
        "provider": "bybit_linear_futures",
        "symbol": "BTCUSDT",
        "state": state,
        "observed_at": now.isoformat(),
        "last_error": None,
        "persistence_state": persistence_state,
        "persistence_error": None,
    }


def test_market_data_health_reports_fresh_stream_and_open_interest():
    now = datetime(2026, 11, 22, 12, tzinfo=UTC)
    stream = FakeFeed(
        "bybit_linear_futures",
        _snapshot("live_market", now - timedelta(seconds=2)),
    )
    collector = FakeFeed(
        "bybit_linear_futures",
        _snapshot("open_interest", now - timedelta(seconds=70)),
        interval_seconds=60,
    )

    result = build_market_data_health(
        "bybit_linear_futures",
        {"stream": stream},
        {"oi": collector},
        now=now,
    )

    assert result["status"] == "healthy"
    assert result["active_feeds"] == 2
    assert {feed["kind"] for feed in result["feeds"]} == {"live_market", "open_interest"}
    assert all(feed["status"] == "healthy" for feed in result["feeds"])


def test_market_data_health_marks_stale_stream_degraded():
    now = datetime(2026, 11, 22, 12, tzinfo=UTC)
    stream = FakeFeed(
        "bybit_linear_futures",
        _snapshot("live_market", now - timedelta(seconds=20)),
    )

    result = build_market_data_health(
        "bybit_linear_futures", {"stream": stream}, {}, now=now
    )

    assert result["status"] == "degraded"
    assert result["feeds"][0]["status"] == "stale"
    assert result["feeds"][0]["is_stale"] is True
    assert result["degraded_reasons"] == ["live_market:BTCUSDT:stale"]


def test_market_data_health_marks_old_open_interest_and_storage_failure():
    now = datetime(2026, 11, 22, 12, tzinfo=UTC)
    collector = FakeFeed(
        "bybit_linear_futures",
        _snapshot(
            "open_interest",
            now - timedelta(seconds=181),
            persistence_state="unavailable",
        ),
        interval_seconds=60,
    )

    result = build_market_data_health(
        "bybit_linear_futures", {}, {"oi": collector}, now=now
    )

    assert result["status"] == "degraded"
    assert result["feeds"][0]["status"] == "stale"
    assert "open_interest:BTCUSDT:persistence_unavailable" in result["degraded_reasons"]


def test_market_data_health_distinguishes_starting_idle_and_provider_filtering():
    now = datetime(2026, 11, 22, 12, tzinfo=UTC)
    connecting = FakeFeed(
        "bybit_linear_futures",
        _snapshot("live_market", now, state="connecting") | {"observed_at": None},
    )
    other_provider = FakeFeed(
        "binance_usdm_futures",
        _snapshot("live_market", now),
    )

    starting = build_market_data_health(
        "bybit_linear_futures",
        {"connecting": connecting, "other": other_provider},
        {},
        now=now,
    )
    idle = build_market_data_health("bybit_linear_futures", {}, {}, now=now)

    assert starting["status"] == "starting"
    assert len(starting["feeds"]) == 1
    assert idle["status"] == "idle"
    assert idle["active_feeds"] == 0


def test_market_data_health_rejects_naive_check_time():
    with pytest.raises(ValueError, match="timezone-aware"):
        build_market_data_health(
            "bybit_linear_futures", {}, {}, now=datetime(2026, 11, 22, 12)
        )


def _authenticated_client(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    return client


def test_market_data_health_route_requires_authentication():
    response = TestClient(app).get("/api/v1/health/market-data")
    assert response.status_code == 401


def test_market_data_health_route_reports_selected_provider(monkeypatch):
    now = datetime.now(UTC)
    stream = FakeFeed(
        "bybit_linear_futures",
        _snapshot("live_market", now),
    )
    monkeypatch.setattr(settings, "futures_market_data_provider", "bybit_linear_futures")
    monkeypatch.setattr(health_routes, "_streams", {"bybit:BTCUSDT": stream})
    monkeypatch.setattr(health_routes, "_collectors", {})

    response = _authenticated_client(monkeypatch).get("/api/v1/health/market-data")

    assert response.status_code == 200
    assert response.json()["provider"] == "bybit_linear_futures"
    assert response.json()["status"] == "healthy"
