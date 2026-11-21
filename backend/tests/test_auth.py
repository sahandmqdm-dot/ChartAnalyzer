from fastapi.testclient import TestClient

from app.auth import _session_token, _verify
from app.main import app
from app.settings import settings

client = TestClient(app)


def test_login_and_me(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "correct-test-password")
    monkeypatch.setattr(settings, "admin_username", "admin")
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "correct-test-password"})
    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert "ca_session" in response.cookies
    assert "ca_csrf" in response.cookies
    me_response = client.get("/api/v1/auth/me", cookies=response.cookies)
    assert me_response.status_code == 200
    assert me_response.json()["username"] == "admin"


def test_invalid_credentials(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "correct-test-password")
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong-password"})
    assert response.status_code == 401


def test_logout_requires_csrf(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "correct-test-password")
    login_response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "correct-test-password"})
    response = client.post("/api/v1/auth/logout", cookies=login_response.cookies)
    assert response.status_code == 403


def test_logout_revokes_session(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", "correct-test-password")
    login_response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "correct-test-password"})
    csrf = login_response.json()["csrf_token"]
    logout_response = client.post(
        "/api/v1/auth/logout",
        cookies=login_response.cookies,
        headers={"X-CSRF-Token": csrf},
    )
    assert logout_response.status_code == 200
    me_response = client.get("/api/v1/auth/me", cookies=login_response.cookies)
    assert me_response.status_code == 401


def test_signed_session_validates():
    assert _verify(_session_token("admin"))["sub"] == "admin"


def test_session_rejects_bad_signature():
    assert _verify("not-a-valid-token") is None
