from fastapi import APIRouter, HTTPException, Query, Request

from app.auth import current_user
from app.market_data.cache import AsyncTTLCache
from app.market_data.futures import FuturesMarketDataError
from app.market_data.futures_routes import get_futures_provider
from app.market_data.mark_price import (
    BinanceUsdmMarkPriceAdapter,
    BybitLinearMarkPriceAdapter,
)
from app.market_data.symbol import Symbol

router = APIRouter(prefix="/api/v1/derivatives", tags=["derivatives"])
_mark_price_cache: AsyncTTLCache = AsyncTTLCache(ttl_seconds=30, max_entries=256)
_PERIODS = {"5m", "15m", "30m", "1h", "4h", "1d"}


@router.get("/mark-price-history")
async def mark_price_history(
    request: Request,
    symbol: str = Query(min_length=2, max_length=30),
    period: str = Query(default="1h"),
    limit: int = Query(default=100, ge=1, le=200),
):
    """Return read-only historical linear-perpetual mark-price OHLC candles."""
    current_user(request)
    try:
        normalized = str(Symbol(symbol))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if period not in _PERIODS:
        raise HTTPException(status_code=422, detail="Unsupported mark price period")

    configured = get_futures_provider()
    if configured.name == "binance_usdm_futures":
        adapter = BinanceUsdmMarkPriceAdapter()
    elif configured.name == "bybit_linear_futures":
        adapter = BybitLinearMarkPriceAdapter()
    else:
        raise HTTPException(status_code=503, detail="No mark price provider configured")

    cache_key = f"{adapter.name}|{normalized}|{period}|{limit}"

    async def load():
        try:
            return await adapter.fetch_mark_price_history(normalized, period, limit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except FuturesMarketDataError as exc:
            raise HTTPException(
                status_code=502,
                detail="Mark price history provider unavailable or returned invalid data",
            ) from exc

    result = await _mark_price_cache.get_or_create(cache_key, load)
    return result.model_dump(mode="json")
