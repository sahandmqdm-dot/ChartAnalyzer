from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.auth import _session_token
from app.main import app
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
