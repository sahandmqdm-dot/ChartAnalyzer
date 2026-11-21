import secrets

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.auth import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    _allow_login,
    _password_matches,
    _session_token,
    csrf_check,
    current_user,
    revoke_session,
)
from app.backtest.routes import router as backtest_router
from app.market_data.futures_analysis_routes import router as derivatives_analysis_router
from app.market_data.futures_history_routes import router as derivatives_history_router
from app.market_data.futures_risk_routes import router as futures_risk_router
from app.market_data.futures_routes import router as derivatives_router
from app.market_data.mark_price_routes import router as mark_price_router
from app.market_data.routes import router as market_data_router
from app.settings import settings

app = FastAPI(
    title="ChartAnalyzer Next API",
    version="0.1.0",
    docs_url="/docs" if settings.environment == "development" else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.environment == "development" else None,
)

app.include_router(market_data_router)
app.include_router(derivatives_router)
app.include_router(futures_risk_router)
app.include_router(derivatives_history_router)
app.include_router(mark_price_router)
app.include_router(derivatives_analysis_router)
app.include_router(backtest_router)

if settings.environment == "development":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )


class LoginPayload(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


@app.get("/api/v1/health", tags=["system"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "chartanalyzer-next"}


@app.post("/api/v1/auth/login", tags=["auth"])
def login(payload: LoginPayload, request: Request, response: Response):
    ip = request.client.host if request.client else "unknown"
    if not _allow_login(ip):
        raise HTTPException(status_code=429, detail="Too many login attempts")
    if not settings.admin_password:
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    valid_user = secrets.compare_digest(payload.username, settings.admin_username)
    valid_password = _password_matches(payload.password)
    if not (valid_user and valid_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    csrf = secrets.token_urlsafe(32)
    secure = settings.environment == "production"
    response.set_cookie(
        SESSION_COOKIE, _session_token(payload.username), max_age=settings.session_ttl_seconds,
        httponly=True, secure=secure, samesite="strict", path="/",
    )
    response.set_cookie(
        CSRF_COOKIE, csrf, max_age=settings.session_ttl_seconds,
        httponly=False, secure=secure, samesite="strict", path="/",
    )
    return {"authenticated": True, "username": payload.username, "csrf_token": csrf}


@app.get("/api/v1/auth/me", tags=["auth"])
def me(request: Request):
    return {"authenticated": True, "username": current_user(request)}


@app.post("/api/v1/auth/logout", tags=["auth"])
def logout(request: Request, response: Response):
    current_user(request)
    csrf_check(request)
    revoke_session(request.cookies.get(SESSION_COOKIE, ""))
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=settings.environment == "production", samesite="strict")
    response.delete_cookie(CSRF_COOKIE, path="/", secure=settings.environment == "production", samesite="strict")
    return {"authenticated": False}
