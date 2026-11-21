from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from app.auth import _session_token
from app.main import app
from app.market_data.candle import Candle
from app.market_data.registry import get_market_data_provider
from app.settings import settings


class FakeBacktestProvider:
    name = "fake"

    async def fetch_ohlcv(self, *args, **kwargs):
        raise AssertionError("backtest must request historical candles")

    async def fetch_historical_ohlcv(self, *args, **kwargs):
        start = datetime(2026, 1, 1, tzinfo=UTC)
        closes = [5, 4, 3, 4, 5, 6, 8]
        result = []
        for index, value in enumerate(closes):
            price = Decimal(value)
            result.append(
                Candle(
                    timestamp=start + timedelta(hours=index),
                    close_time=start + timedelta(hours=index + 1) - timedelta(milliseconds=1),
                    is_closed=True,
                    open=price,
                    high=price + Decimal(1),
                    low=price - Decimal(1),
                    close=price,
                    volume=Decimal(10),
                )
            )
        return result


def test_backtest_endpoint_requires_authentication():
    client = TestClient(app)
    response = client.get(
        "/api/v1/backtest/sma-crossover",
        params={
            "symbol": "BTCUSDT",
            "start_time": "2026-01-01T00:00:00Z",
            "end_time": "2026-01-02T00:00:00Z",
            "fast_period": 2,
            "slow_period": 3,
        },
    )
    assert response.status_code == 401


def test_backtest_endpoint_runs_against_historical_provider(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    app.dependency_overrides[get_market_data_provider] = FakeBacktestProvider
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    try:
        response = client.get(
            "/api/v1/backtest/sma-crossover",
            params={
                "symbol": "btcusdt",
                "interval": "1h",
                "start_time": "2026-01-01T00:00:00Z",
                "end_time": "2026-01-02T00:00:00Z",
                "fast_period": 2,
                "slow_period": 3,
                "initial_equity": "1000",
                "fee_rate": "0",
                "slippage_rate": "0",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "fake"
    assert body["symbol"] == "BTCUSDT"
    assert body["candles_used"] == 7
    assert body["result"]["trade_count"] == 1
    assert body["result"]["trades"][0]["entry_price"] == "6"
    assert len(body["result"]["equity_curve"]) == body["candles_used"]
    assert "profit_factor" in body["result"]
    assert "exposure_pct" in body["result"]


def test_backtest_endpoint_rejects_invalid_periods(monkeypatch):
    monkeypatch.setattr(settings, "admin_username", "admin")
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    response = client.get(
        "/api/v1/backtest/sma-crossover",
        params={
            "symbol": "BTCUSDT",
            "start_time": "2026-01-01T00:00:00Z",
            "end_time": "2026-01-02T00:00:00Z",
            "fast_period": 5,
            "slow_period": 3,
        },
    )
    assert response.status_code == 422
