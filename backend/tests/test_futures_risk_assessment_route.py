from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from app.auth import _session_token
from app.main import app
from app.market_data.futures import FuturesOverview
from app.market_data.futures_risk_routes import (
    BybitLinearRiskTierAdapter,
)
from app.market_data.risk_tiers import RiskTierSet
from app.settings import settings


class FakeBybitProvider:
    name = "bybit_linear_futures"

    async def fetch_overview(self, symbol: str):
        return FuturesOverview(
            provider=self.name,
            market_type="linear_perpetual",
            symbol=symbol,
            mark_price=Decimal("95"),
            index_price=Decimal("94.9"),
            funding_rate=Decimal("0.0001"),
            open_interest=Decimal("100"),
            open_interest_value=Decimal("9500"),
            next_funding_time=None,
            observed_at=datetime.now(UTC),
        )


async def fake_tiers(self, symbol: str):
    from app.market_data.futures_risk import RiskTier

    return RiskTierSet(
        provider="bybit_linear_futures",
        category="linear",
        symbol=symbol,
        tiers=[
            RiskTier(
                tier=1,
                notional_floor=Decimal(0),
                notional_cap=Decimal("100000"),
                maintenance_margin_rate=Decimal("0.005"),
                maintenance_margin_deduction=Decimal(0),
                max_leverage=Decimal(100),
            )
        ],
    )


def test_live_risk_assessment_requires_authentication():
    response = TestClient(app).post(
        "/api/v1/derivatives/risk-assessment/live",
        json={
            "symbol": "BTCUSDT",
            "side": "long",
            "entry_price": "100",
            "quantity": "1",
            "leverage": "10",
        },
    )
    assert response.status_code == 401


def test_live_risk_assessment_uses_exchange_mark_price_and_tiers(monkeypatch):
    from app.market_data import futures_risk_routes

    monkeypatch.setattr(settings, "admin_username", "admin")
    monkeypatch.setattr(
        futures_risk_routes, "get_futures_provider", lambda: FakeBybitProvider()
    )
    monkeypatch.setattr(
        BybitLinearRiskTierAdapter, "fetch_risk_tiers", fake_tiers
    )
    client = TestClient(app)
    client.cookies.set("ca_session", _session_token("admin"))
    response = client.post(
        "/api/v1/derivatives/risk-assessment/live",
        json={
            "symbol": "btcusdt",
            "side": "long",
            "entry_price": "100",
            "quantity": "1",
            "leverage": "10",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "bybit_linear_futures"
    assert body["assessment_status"] == "live_exchange_data"
    assert body["risk_tiers_source"] == "exchange"
    assert body["mark_price"] == "95"
    assert body["estimate"]["mark_price"] == "95"
    assert body["alert"]["level"] in {
        "normal", "warning", "critical", "liquidation_near"
    }
