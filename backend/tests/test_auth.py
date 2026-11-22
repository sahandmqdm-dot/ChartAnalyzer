import json
import time
from collections import OrderedDict

from fastapi.testclient import TestClient

from app import auth
from app.auth import _allow_login, _b64, _session_token, _sign, _verify, revoke_session
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


def test_session_rejects_future_issued_at():
    payload = _b64(
        json.dumps(
            {"sub": "admin", "iat": int(time.time()) + 3600, "sid": "future-session"}
        ).encode()
    )
    assert _verify(_sign(payload)) is None


def test_session_rejects_missing_session_id():
    payload = _b64(
        json.dumps({"sub": "admin", "iat": int(time.time())}).encode()
    )
    assert _verify(_sign(payload)) is None


def test_session_rejects_oversized_token():
    assert _verify("x" * 5000) is None


def test_login_attempts_are_limited_per_ip_and_expire(monkeypatch):
    monkeypatch.setattr(auth, "_attempts", OrderedDict())
    monkeypatch.setattr(auth.time, "time", lambda: 100.0)
    assert all(_allow_login("192.0.2.10") for _ in range(5))
    assert _allow_login("192.0.2.10") is False

    monkeypatch.setattr(auth.time, "time", lambda: 161.0)
    assert _allow_login("192.0.2.10") is True


def test_login_attempt_tracking_has_a_bounded_ip_table(monkeypatch):
    monkeypatch.setattr(auth, "_attempts", OrderedDict())
    monkeypatch.setattr(auth, "_MAX_LOGIN_IPS", 2)
    monkeypatch.setattr(auth.time, "time", lambda: 100.0)

    assert _allow_login("192.0.2.1")
    assert _allow_login("192.0.2.2")
    assert _allow_login("192.0.2.3")
    assert len(auth._attempts) == 2
    assert "192.0.2.1" not in auth._attempts


def test_revoked_session_entries_are_removed_after_expiry(monkeypatch):
    monkeypatch.setattr(auth, "_revoked_sessions", {})
    monkeypatch.setattr(auth, "_revoked_expirations", [])
    token = _session_token("admin")
    session = _verify(token)
    assert session is not None
    session_id = session["sid"]

    revoke_session(token)
    assert session_id in auth._revoked_sessions
    monkeypatch.setattr(
        auth.time,
        "time",
        lambda: session["iat"] + settings.session_ttl_seconds + 1,
    )

    assert _verify(token) is None
    assert session_id not in auth._revoked_sessions
