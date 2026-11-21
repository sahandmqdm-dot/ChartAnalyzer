from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import current_user
from app.market_data.binance import MarketDataProviderError
from app.market_data.cache import AsyncTTLCache
from app.market_data.candle import Candle
from app.market_data.provider import MarketDataProvider
from app.market_data.registry import get_market_data_provider

router = APIRouter(prefix="/api/v1/market-data", tags=["market-data"])
_candle_cache: AsyncTTLCache[tuple[Candle, ...]] = AsyncTTLCache(ttl_seconds=15, max_entries=512)


@router.get("/candles")
async def candles(
    request: Request,
    symbol: str = Query(min_length=2, max_length=30),
    interval: str = Query(default="1h", max_length=5),
    limit: int = Query(default=500, ge=1, le=1000),
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    adapter: MarketDataProvider = Depends(get_market_data_provider),  # noqa: B008
):
    current_user(request)
    normalized_symbol = symbol.strip().upper()
    cache_key = "|".join(
        (
            normalized_symbol,
            interval,
            str(limit),
            start_time.isoformat() if start_time else "",
            end_time.isoformat() if end_time else "",
        )
    )
    values = await _candle_cache.get(cache_key)
    if values is None:
        try:
            fetched = await adapter.fetch_ohlcv(
                normalized_symbol,
                interval,
                limit=limit,
                start_time=start_time,
                end_time=end_time,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except MarketDataProviderError as exc:
            raise HTTPException(status_code=502, detail="Market data provider unavailable or invalid") from exc
        values = tuple(fetched)
        await _candle_cache.set(cache_key, values)
    return {
        "provider": adapter.name,
        "symbol": normalized_symbol,
        "interval": interval,
        "count": len(values),
        "candles": [candle.model_dump(mode="json") for candle in values],
    }
