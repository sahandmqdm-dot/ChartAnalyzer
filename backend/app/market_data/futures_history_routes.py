from fastapi import APIRouter, HTTPException, Query, Request

from app.auth import current_user
from app.market_data.cache import AsyncTTLCache
from app.market_data.futures import FuturesMarketDataError
from app.market_data.futures_history import (
    BinanceUsdmFuturesHistoryAdapter,
    BybitLinearFuturesHistoryAdapter,
)
from app.market_data.futures_routes import get_futures_provider
from app.market_data.symbol import Symbol

router = APIRouter(prefix="/api/v1/derivatives", tags=["derivatives"])
_history_cache: AsyncTTLCache = AsyncTTLCache(ttl_seconds=60, max_entries=256)
_PERIODS = {"5m", "15m", "30m", "1h", "4h", "1d"}


@router.get("/history")
async def futures_history(
    request: Request,
    symbol: str = Query(min_length=2, max_length=30),
    period: str = Query(default="1h"),
    limit: int = Query(default=100, ge=1, le=200),
):
    """Return historical funding, open-interest, and global long/short series."""
    current_user(request)
    try:
        normalized = str(Symbol(symbol))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if period not in _PERIODS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported period. Choose one of: {', '.join(sorted(_PERIODS))}",
        )

    configured = get_futures_provider()
    if configured.name == "binance_usdm_futures":
        adapter = BinanceUsdmFuturesHistoryAdapter()
    elif configured.name == "bybit_linear_futures":
        adapter = BybitLinearFuturesHistoryAdapter()
    else:
        raise HTTPException(status_code=503, detail="No historical futures provider configured")

    cache_key = f"{adapter.name}|{normalized}|{period}|{limit}"

    async def load_history():
        try:
            return await adapter.fetch_history(normalized, period, limit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except FuturesMarketDataError as exc:
            raise HTTPException(
                status_code=502,
                detail="Futures history provider unavailable or returned invalid data",
            ) from exc

    result = await _history_cache.get_or_create(cache_key, load_history)
    return result.model_dump(mode="json")
