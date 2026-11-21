from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from app.auth import _session_token
from app.market_data.candle import Candle
from app.market_data.futures_history import FuturesHistory, FuturesSeriesPoint
from app.main import app
from app.settings import settings


class FakeHistoryAdapter:
    async def fetch_history(self, symbol: str, period: str, limit: int) -> FuturesHistory:
        points = [
            FuturesSeriesPoint(timestamp=datetime(2026, 11, 21, hour, tzinfo=UTC), value=Decimal(value))
            for hour, value in [(10, "100"), (11, "110")]
        ]
        return FuturesHistory(
            provider="binance_usdm_futures",
            market_type="linear_perpetual",
            symbol=symbol,
            period=period,
            funding_rate=[FuturesSeriesPoint(
                timestamp=datetime(2026, 11, 21, 11, tzinfo=UTC), value=Decimal("0.0001")
            )],
            open_interest=points,
            long_short_ratio=[FuturesSeriesPoint(
                timestamp=datetime(2026, 11, 21, 11, tzinfo=UTC), value=Decimal("1.1")
            )],
            observed_at=datetime(2026, 11, 21, 12, tzinfo=UTC),
        )


class FakeFuturesProvider:
    name = "binance_usdm_futures"


class FakeSpotProvider:
    async def fetch_ohlcv(self, symbol: str, interval: str, *, limit: int):
        return [
            Candle(
                timestamp=datetime(2026, 11, 21, hour, tzinfo=UTC),
                open=Decimal(value), high=Decimal(value), low=Decimal(value),
                close=Decimal(value), volume=Decimal("1"),
            )
            for hour, value in [(10, "100"), (11, "105")]
        ]


def test_futures_analysis_requires_authentication():
    response = TestClient(app).get(
        "/api/v1/derivatives/analysis", params={"symbol": "BTCUSDT"}
    )
    assert response.status_code == 401


def test_futures_analysis_returns_descriptive_context(monkeypatch):
    from app.market_data import futures_analysis_routes

    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(futures_analysis_routes, "get_futures_provider", FakeFuturesProvider)
    monkeypatch.setattr(
        futures_analysis_routes, "BinanceUsdmFuturesHistoryAdapter", FakeHistoryAdapter
    )
    monkeypatch.setattr(
        futures_analysis_routes, "get_market_data_provider", FakeSpotProvider
    )
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    response = client.get(
        "/api/v1/derivatives/analysis",
        params={"symbol": "btcusdt", "period": "1h"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["symbol"] == "BTCUSDT"
    assert body["positioning"] == "long_and_short_build_up_with_rising_price"
    assert body["price_change_pct"] == "5"
    assert body["open_interest_change_pct"] == "10"
    assert any("Spot" in caveat for caveat in body["caveats"])
