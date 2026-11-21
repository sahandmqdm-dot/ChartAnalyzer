from fastapi import APIRouter, HTTPException, Query, Request

from app.auth import current_user
from app.market_data.cache import AsyncTTLCache
from app.market_data.futures import FuturesMarketDataError
from app.market_data.futures_risk import (
    IsolatedRiskEstimateRequest,
    estimate_isolated_liquidation,
)
from app.market_data.futures_routes import get_futures_provider
from app.market_data.risk_tiers import BybitLinearRiskTierAdapter
from app.market_data.symbol import Symbol

router = APIRouter(prefix="/api/v1/derivatives", tags=["derivatives"])
_tier_cache: AsyncTTLCache = AsyncTTLCache(ttl_seconds=300, max_entries=256)


@router.get("/risk-tiers")
async def risk_tiers(request: Request, symbol: str = Query(min_length=2, max_length=30)):
    """Return public exchange risk tiers when the configured venue exposes them."""
    current_user(request)
    try:
        normalized = str(Symbol(symbol))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    configured = get_futures_provider()
    if configured.name != "bybit_linear_futures":
        raise HTTPException(
            status_code=501,
            detail=(
                "Automatic public risk-tier lookup is unavailable for this venue. "
                "Binance leverage brackets require a signed account API request; "
                "supply verified brackets from the account's Binance API response."
            ),
        )

    async def load():
        try:
            return await BybitLinearRiskTierAdapter().fetch_risk_tiers(normalized)
        except FuturesMarketDataError as exc:
            raise HTTPException(
                status_code=502, detail="Risk-tier provider unavailable or returned invalid data"
            ) from exc

    result = await _tier_cache.get_or_create(f"{configured.name}|{normalized}", load)
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
