from fastapi import APIRouter, HTTPException, Query, Request

from app.auth import current_user
from app.market_data.cache import AsyncTTLCache
from app.market_data.futures import FuturesMarketDataError
from app.market_data.futures_routes import get_futures_provider
from app.market_data.mark_price import (
    BinanceUsdmMarkPriceAdapter,
    BybitLinearMarkPriceAdapter,
)
from app.market_data.market_structure import analyze_market_structure
from app.market_data.symbol import Symbol

router = APIRouter(prefix="/api/v1/derivatives", tags=["derivatives"])
_cache: AsyncTTLCache = AsyncTTLCache(ttl_seconds=30, max_entries=256)
_PERIODS = {"5m", "15m", "30m", "1h", "4h", "1d"}


@router.get("/market-structure")
async def market_structure(
    request: Request,
    symbol: str = Query(min_length=2, max_length=30),
    period: str = Query(default="1h"),
    limit: int = Query(default=200, ge=20, le=200),
    pivot_window: int = Query(default=3, ge=2, le=10),
):
    """Return rule-based market structure from closed linear-perpetual mark-price candles."""
    current_user(request)
    try:
        normalized = str(Symbol(symbol))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if period not in _PERIODS:
        raise HTTPException(status_code=422, detail="Unsupported market-structure period")

    configured = get_futures_provider()
    if configured.name == "binance_usdm_futures":
        adapter = BinanceUsdmMarkPriceAdapter()
    elif configured.name == "bybit_linear_futures":
        adapter = BybitLinearMarkPriceAdapter()
    else:
        raise HTTPException(status_code=503, detail="No mark price provider configured")

    key = f"{adapter.name}|{normalized}|{period}|{limit}|{pivot_window}"

    async def load():
        try:
            history = await adapter.fetch_mark_price_history(normalized, period, limit)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except FuturesMarketDataError as exc:
            raise HTTPException(
                status_code=502,
                detail="Market-structure provider unavailable or returned invalid data",
            ) from exc
        return analyze_market_structure(
            symbol=normalized,
            period=period,
            provider=history.provider,
            candles=history.candles,
            observed_at=history.observed_at,
            pivot_window=pivot_window,
        ).model_dump(mode="json")

    return await _cache.get_or_create(key, load)
