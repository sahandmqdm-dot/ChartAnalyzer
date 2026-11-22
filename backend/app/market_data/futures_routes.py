from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import current_user
from app.market_data.cache import AsyncTTLCache
from app.market_data.futures import (
    BinanceUsdmFuturesAdapter,
    BybitLinearFuturesAdapter,
    FuturesMarketDataError,
    FuturesMarketDataProvider,
)
from app.market_data.futures_risk import (
    IsolatedRiskEstimateRequest,
    estimate_isolated_liquidation,
)
from app.market_data.symbol import Symbol
from app.settings import settings

router = APIRouter(prefix="/api/v1/derivatives", tags=["derivatives"])
_overview_cache: AsyncTTLCache = AsyncTTLCache(ttl_seconds=10, max_entries=256)


def get_futures_provider() -> FuturesMarketDataProvider:
    """Select the configured linear-perpetual provider independently of Spot."""
    providers: dict[str, type[FuturesMarketDataProvider]] = {
        "binance_usdm_futures": BinanceUsdmFuturesAdapter,
        "bybit_linear_futures": BybitLinearFuturesAdapter,
    }
    provider_type = providers.get(settings.futures_market_data_provider)
    if provider_type is None:
        raise RuntimeError("No supported futures market-data provider is configured")
    return provider_type()


@router.get("/overview")
async def futures_overview(
    request: Request,
    symbol: str = Query(min_length=2, max_length=30),
    adapter: FuturesMarketDataProvider = Depends(get_futures_provider),  # noqa: B008
):
    """Return public linear-perpetual market context; no trading endpoints are exposed."""
    current_user(request)
    try:
        normalized_symbol = str(Symbol(symbol))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    cache_key = f"{adapter.name}|linear_perpetual|{normalized_symbol}"

    async def load_overview():
        try:
            return await adapter.fetch_overview(normalized_symbol)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except FuturesMarketDataError as exc:
            raise HTTPException(
                status_code=502,
                detail="Futures market-data provider unavailable or invalid",
            ) from exc

    result = await _overview_cache.get_or_create(cache_key, load_overview)
    return result.model_dump(mode="json")



@router.post("/risk/isolated-liquidation-estimate")
async def isolated_liquidation_estimate(
    request: Request,
    payload: IsolatedRiskEstimateRequest,
):
    """Calculate an estimate only; this endpoint cannot place or modify orders."""
    current_user(request)
    try:
        result = estimate_isolated_liquidation(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result.model_dump(mode="json")
