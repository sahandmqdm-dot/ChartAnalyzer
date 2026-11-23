from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.auth import _session_token
from app.main import app
from app.market_data.coinalyze import CoinalyzeSnapshot
from app.settings import settings


def test_coinalyze_snapshot_requires_authentication():
    response = TestClient(app).get(
        "/api/v1/derivatives/coinalyze/snapshot",
        params={"symbols": "BTC.H"},
    )
    assert response.status_code == 401


def test_coinalyze_snapshot_requires_api_key(monkeypatch):
    monkeypatch.setattr(settings, "coinalyze_api_key", None)
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    response = client.get(
        "/api/v1/derivatives/coinalyze/snapshot",
        params={"symbols": "BTC.H"},
    )
    assert response.status_code == 503


def test_coinalyze_snapshot_rejects_more_than_twenty_symbols(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "coinalyze_api_key", SecretStr("test-key"))
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    symbols = ",".join(f"BTC{i}.A" for i in range(21))
    response = client.get(
        "/api/v1/derivatives/coinalyze/snapshot",
        params={"symbols": symbols},
    )
    assert response.status_code == 422


def test_coinalyze_snapshot_returns_authenticated_normalized_data(monkeypatch):
    from app.market_data import coinalyze_routes

    class FakeCoinalyzeClient:
        def __init__(self, api_key):
            assert api_key == "test-key"

        async def fetch_snapshot(self, symbols):
            assert symbols == ["BTC.H"]
            return [
                CoinalyzeSnapshot(
                    symbol="BTC.H",
                    open_interest=Decimal("37728.76524"),
                    open_interest_updated_at=datetime(2026, 11, 23, 16, 54, 56, tzinfo=UTC),
                    funding_rate=Decimal("0.00021097"),
                    funding_rate_updated_at=datetime(2026, 11, 23, 16, 54, 58, tzinfo=UTC),
                    observed_at=datetime(2026, 11, 23, 16, 55, 0, tzinfo=UTC),
                )
            ]

    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(settings, "coinalyze_api_key", SecretStr("test-key"))
    monkeypatch.setattr(coinalyze_routes, "CoinalyzeClient", FakeCoinalyzeClient)
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    response = client.get(
        "/api/v1/derivatives/coinalyze/snapshot",
        params={"symbols": "btc.h"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "coinalyze"
    assert body["data"][0]["symbol"] == "BTC.H"
    assert body["data"][0]["open_interest"] == "37728.76524"
    assert body["data"][0]["funding_rate"] == "0.00021097"
