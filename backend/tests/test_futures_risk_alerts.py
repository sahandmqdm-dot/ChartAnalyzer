from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.market_data.futures_risk import (
    IsolatedRiskEstimateRequest,
    RiskTier,
    estimate_isolated_liquidation,
)
from app.market_data.futures_risk_alerts import assess_futures_risk


def estimate_at_distance(distance: str):
    tiers = [
        RiskTier(
            tier=1,
            notional_floor=Decimal(0),
            notional_cap=None,
            maintenance_margin_rate=Decimal("0.01"),
        )
    ]
    base = estimate_isolated_liquidation(
        IsolatedRiskEstimateRequest(
            symbol="BTCUSDT",
            side="long",
            entry_price=Decimal(100),
            mark_price=Decimal(95),
            quantity=Decimal(1),
            leverage=Decimal(10),
            risk_tiers=tiers,
        )
    )
    return base.model_copy(
        update={"distance_to_liquidation_from_mark_percent": Decimal(distance)}
    )


@pytest.mark.parametrize(
    ("distance", "expected"),
    [
        ("12", "normal"),
        ("10", "warning"),
        ("7", "warning"),
        ("5", "critical"),
        ("2", "critical"),
        ("1", "liquidation_near"),
        ("0", "liquidation_near"),
        ("-2", "liquidation_near"),
    ],
)
def test_risk_alert_levels_follow_signed_mark_distance(distance, expected):
    now = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    result = assess_futures_risk(
        provider="bybit_linear_futures",
        mark_price=Decimal(95),
        mark_price_observed_at=now,
        risk_tiers_observed_at=now,
        estimate=estimate_at_distance(distance),
        now=now,
    )

    assert result.alert.level == expected
    assert result.alert.distance_percent == Decimal(distance)
    assert result.freshness_seconds == 0
    assert result.risk_tiers_source == "exchange"
    assert result.assessment_status == "live_exchange_data"


def test_risk_alert_rejects_naive_timestamps():
    with pytest.raises(ValueError, match="timezone-aware"):
        assess_futures_risk(
            provider="bybit_linear_futures",
            mark_price=Decimal(95),
            mark_price_observed_at=datetime(2026, 11, 22, 12, 0),  # noqa: DTZ001
            risk_tiers_observed_at=datetime(2026, 11, 22, 12, 0, tzinfo=UTC),
            estimate=estimate_at_distance("5"),
        )
