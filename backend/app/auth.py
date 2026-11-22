import base64
import hashlib
import heapq
import hmac
import json
import secrets
import time
from collections import OrderedDict, deque

from fastapi import HTTPException, Request

from app.settings import settings

SESSION_COOKIE = "ca_session"
CSRF_COOKIE = "ca_csrf"
_LOGIN_LIMIT = 5
_LOGIN_WINDOW_SECONDS = 60
_MAX_LOGIN_IPS = 10_000
_attempts: OrderedDict[str, deque[float]] = OrderedDict()
_revoked_sessions: dict[str, int] = {}
_revoked_expirations: list[tuple[int, str]] = []


def _purge_revoked_sessions(now: int) -> None:
    while _revoked_expirations and _revoked_expirations[0][0] <= now:
        expires_at, session_id = heapq.heappop(_revoked_expirations)
        if _revoked_sessions.get(session_id) == expires_at:
            del _revoked_sessions[session_id]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(payload: str) -> str:
    mac = hmac.new(settings.app_secret.encode(), payload.encode(), hashlib.sha256).digest()
    return f"{payload}.{_b64(mac)}"


def _verify(token: str) -> dict | None:
    if not token or len(token) > 4096:
        return None
    try:
        payload, signature = token.rsplit(".", 1)
        expected = hmac.new(settings.app_secret.encode(), payload.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(signature)):
            return None
        data = json.loads(_unb64(payload))
        if not isinstance(data, dict):
            return None
        issued_at = data.get("iat")
        session_id = data.get("sid")
        if isinstance(issued_at, bool) or not isinstance(issued_at, int):
            return None
        now = int(time.time())
        _purge_revoked_sessions(now)
        if issued_at > now + 30 or now - issued_at > settings.session_ttl_seconds:
            return None
        if data.get("sub") != settings.admin_username or not isinstance(session_id, str) or not session_id:
            return None
        if session_id in _revoked_sessions:
            return None
        return data
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _session_token(username: str) -> str:
    payload = _b64(json.dumps({"sub": username, "iat": int(time.time()), "sid": secrets.token_urlsafe(24)}, separators=(",", ":")).encode())
    return _sign(payload)


def _password_matches(password: str) -> bool:
    # PBKDF2 is intentionally CPU-bounded for this small local bootstrap account.
    expected = hashlib.pbkdf2_hmac("sha256", settings.admin_password.encode(), settings.app_secret.encode(), 200_000)
    supplied = hashlib.pbkdf2_hmac("sha256", password.encode(), settings.app_secret.encode(), 200_000)
    return hmac.compare_digest(expected, supplied)


def _allow_login(ip: str) -> bool:
    now = time.time()
    history = _attempts.get(ip)
    if history is None:
        if len(_attempts) >= _MAX_LOGIN_IPS:
            _attempts.popitem(last=False)
        history = deque()
        _attempts[ip] = history
    else:
        _attempts.move_to_end(ip)
    while history and now - history[0] > _LOGIN_WINDOW_SECONDS:
        history.popleft()
    if len(history) >= _LOGIN_LIMIT:
        return False
    history.append(now)
    return True


def current_user(request: Request) -> str:
    token = request.cookies.get(SESSION_COOKIE, "")
    session = _verify(token)
    if session is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return session["sub"]


def csrf_check(request: Request) -> None:
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get("X-CSRF-Token", "")
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise HTTPException(status_code=403, detail="CSRF validation failed")


def revoke_session(token: str) -> None:
    session = _verify(token)
    if session and session.get("sid"):
        session_id = session["sid"]
        expires_at = int(session["iat"]) + settings.session_ttl_seconds
        _revoked_sessions[session_id] = expires_at
        heapq.heappush(_revoked_expirations, (expires_at, session_id))
