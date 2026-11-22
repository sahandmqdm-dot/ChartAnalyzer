from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.auth import current_user
from app.market_data.cache import AsyncTTLCache
from app.market_data.futures import FuturesMarketDataError
from app.market_data.futures_risk import (
    IsolatedRiskEstimateRequest,
    estimate_isolated_liquidation,
)
from app.market_data.futures_risk_alerts import assess_futures_risk
from app.market_data.futures_routes import get_futures_provider
from app.market_data.risk_tiers import BybitLinearRiskTierAdapter
from app.market_data.symbol import Symbol

router = APIRouter(prefix="/api/v1/derivatives", tags=["derivatives"])
_tier_cache: AsyncTTLCache = AsyncTTLCache(ttl_seconds=300, max_entries=256)


class LiveRiskAssessmentRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=2, max_length=30)
    side: Literal["long", "short"]
    entry_price: Decimal = Field(gt=0)
    quantity: Decimal = Field(gt=0)
    leverage: Decimal = Field(ge=1)
    extra_margin: Decimal = Field(default=Decimal(0), ge=0)
    estimated_close_fee_rate: Decimal = Field(default=Decimal("0.0005"), ge=0, lt=1)


async def _exchange_risk_tiers(provider_name: str, symbol: str):
    if provider_name != "bybit_linear_futures":
        raise HTTPException(
            status_code=501,
            detail=(
                "Live exchange risk tiers are currently supported for Bybit Linear only. "
                "Binance leverage brackets require signed account credentials; manual tiers "
                "are deliberately not accepted by this live assessment endpoint."
            ),
        )

    async def load():
        try:
            return await BybitLinearRiskTierAdapter().fetch_risk_tiers(symbol)
        except FuturesMarketDataError as exc:
            raise HTTPException(
                status_code=502,
                detail="Exchange risk-tier provider unavailable or returned invalid data",
            ) from exc

    return await _tier_cache.get_or_create(f"{provider_name}|{symbol}", load)


@router.get("/risk-tiers")
async def risk_tiers(request: Request, symbol: str = Query(min_length=2, max_length=30)):
    """Return public exchange risk tiers when the configured venue exposes them."""
    current_user(request)
    try:
        normalized = str(Symbol(symbol))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    configured = get_futures_provider()
    result = await _exchange_risk_tiers(configured.name, normalized)
    return result.model_dump(mode="json")


@router.post("/risk-estimate")
async def risk_estimate(request: Request, payload: IsolatedRiskEstimateRequest):
    """Calculate a tier-aware isolated linear position liquidation estimate."""
    current_user(request)
    try:
        normalized = str(Symbol(payload.symbol))
        validated = payload.model_copy(update={"symbol": normalized})
        result = estimate_isolated_liquidation(validated)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result.model_dump(mode="json")


@router.post("/risk-assessment/live")
async def live_risk_assessment(request: Request, payload: LiveRiskAssessmentRequest):
    """Assess an isolated position using a fresh mark-price snapshot and exchange tiers."""
    current_user(request)
    try:
        normalized = str(Symbol(payload.symbol))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    provider = get_futures_provider()
    tiers_task = _exchange_risk_tiers(provider.name, normalized)
    try:
        overview, tier_set = await asyncio.gather(
            provider.fetch_overview(normalized),
            tiers_task,
        )
    except FuturesMarketDataError as exc:
        raise HTTPException(
            status_code=502,
            detail="Live mark-price provider unavailable or returned invalid data",
        ) from exc

    if overview.symbol != normalized or overview.mark_price <= 0:
        raise HTTPException(status_code=502, detail="Invalid live mark-price snapshot")

    estimate_request = IsolatedRiskEstimateRequest(
        symbol=normalized,
        side=payload.side,
        entry_price=payload.entry_price,
        mark_price=overview.mark_price,
        quantity=payload.quantity,
        leverage=payload.leverage,
        extra_margin=payload.extra_margin,
        estimated_close_fee_rate=payload.estimated_close_fee_rate,
        risk_tiers=tier_set.tiers,
    )
    try:
        estimate = estimate_isolated_liquidation(estimate_request)
        result = assess_futures_risk(
            provider=provider.name,
            mark_price=overview.mark_price,
            mark_price_observed_at=overview.observed_at,
            risk_tiers_observed_at=tier_set.observed_at,
            estimate=estimate,
            now=datetime.now(UTC),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return result.model_dump(mode="json")
