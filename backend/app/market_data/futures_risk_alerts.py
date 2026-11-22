from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.market_data.futures_risk import IsolatedRiskEstimate


class FuturesRiskAlert(BaseModel):
    model_config = ConfigDict(frozen=True)

    level: Literal["normal", "warning", "critical", "liquidation_near"]
    title: str
    message: str
    distance_percent: Decimal
    triggered: bool


class FuturesRiskAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    symbol: str
    mark_price: Decimal
    mark_price_observed_at: datetime
    risk_tiers_source: Literal["exchange"]
    risk_tiers_observed_at: datetime
    freshness_seconds: int
    assessment_status: Literal["live_exchange_data"]
    estimate: IsolatedRiskEstimate
    alert: FuturesRiskAlert
    thresholds: dict[str, Decimal]


def assess_futures_risk(
    *,
    provider: str,
    mark_price: Decimal,
    mark_price_observed_at: datetime,
    risk_tiers_observed_at: datetime,
    estimate: IsolatedRiskEstimate,
    now: datetime | None = None,
) -> FuturesRiskAssessment:
    """Map signed mark-to-liquidation distance into explainable risk levels."""
    observed_now = now or datetime.now(UTC)
    if observed_now.tzinfo is None or mark_price_observed_at.tzinfo is None:
        raise ValueError("risk assessment timestamps must be timezone-aware")
    distance = estimate.distance_to_liquidation_from_mark_percent
    if distance is None:
        raise ValueError("mark price is required for a live risk assessment")

    thresholds = {
        "warning_percent": Decimal(10),
        "critical_percent": Decimal(5),
        "liquidation_near_percent": Decimal(1),
    }
    if distance <= thresholds["liquidation_near_percent"]:
        level = "liquidation_near"
        title = "Liquidation نزدیک است"
        message = (
            "Mark Price به قیمت تخمینی Liquidation رسیده یا فاصله آن حداکثر ۱٪ است. "
            "این محاسبه تخمینی است و قیمت قطعی صرافی نیست."
        )
    elif distance <= thresholds["critical_percent"]:
        level = "critical"
        title = "ریسک بحرانی"
        message = "فاصله Mark Price تا Liquidation حداکثر ۵٪ است."
    elif distance <= thresholds["warning_percent"]:
        level = "warning"
        title = "هشدار ریسک"
        message = "فاصله Mark Price تا Liquidation حداکثر ۱۰٪ است."
    else:
        level = "normal"
        title = "وضعیت عادی"
        message = "فاصله Mark Price تا Liquidation بیش از ۱۰٪ است."

    age = max(0, int((observed_now - mark_price_observed_at).total_seconds()))
    return FuturesRiskAssessment(
        provider=provider,
        symbol=estimate.symbol,
        mark_price=mark_price,
        mark_price_observed_at=mark_price_observed_at,
        risk_tiers_source="exchange",
        risk_tiers_observed_at=risk_tiers_observed_at,
        freshness_seconds=age,
        assessment_status="live_exchange_data",
        estimate=estimate,
        alert=FuturesRiskAlert(
            level=level,
            title=title,
            message=message,
            distance_percent=distance,
            triggered=level != "normal",
        ),
        thresholds=thresholds,
    )
