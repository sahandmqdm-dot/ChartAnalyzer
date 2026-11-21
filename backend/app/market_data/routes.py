from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import current_user
from app.market_data.cache import AsyncTTLCache
from app.market_data.candle import Candle
from app.market_data.provider import MarketDataProvider, MarketDataProviderError
from app.market_data.registry import get_market_data_provider
from app.market_data.symbol import Symbol
from app.market_data.timeframe import Timeframe

router = APIRouter(prefix="/api/v1/market-data", tags=["market-data"])
_candle_cache: AsyncTTLCache[tuple[Candle, ...]] = AsyncTTLCache(ttl_seconds=15, max_entries=512)


@router.get("/candles")
async def candles(
    request: Request,
    symbol: str = Query(min_length=2, max_length=30),
    interval: str = Query(default="1h", max_length=5),
    limit: int = Query(default=500, ge=1, le=1000),
    closed_only: bool = Query(default=False),
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    adapter: MarketDataProvider = Depends(get_market_data_provider),  # noqa: B008
):
    current_user(request)
    try:
        normalized_symbol = str(Symbol(symbol))
        normalized_interval = Timeframe(interval).value
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    cache_key = "|".join(
        (
            adapter.name,
            normalized_symbol,
            normalized_interval,
            str(limit),
            start_time.isoformat() if start_time else "",
            end_time.isoformat() if end_time else "",
        )
    )
    async def load_candles() -> tuple[Candle, ...]:
        try:
            fetched = await adapter.fetch_ohlcv(
                normalized_symbol,
                normalized_interval,
                limit=limit,
                start_time=start_time,
                end_time=end_time,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except MarketDataProviderError as exc:
            raise HTTPException(status_code=502, detail="Market data provider unavailable or invalid") from exc
        return tuple(fetched)

    values = await _candle_cache.get_or_create(cache_key, load_candles)
    output_values = tuple(candle for candle in values if candle.is_closed) if closed_only else values
    return {
        "provider": adapter.name,
        "symbol": normalized_symbol,
        "interval": normalized_interval,
        "count": len(output_values),
        "candles": [candle.model_dump(mode="json") for candle in output_values],
    }



@router.get("/candles/history")
async def candle_history(
    request: Request,
    symbol: str = Query(min_length=2, max_length=30),
    interval: str = Query(default="1h", max_length=5),
    start_time: datetime = Query(...),  # noqa: B008
    end_time: datetime = Query(...),  # noqa: B008
    page_limit: int = Query(default=1000, ge=1, le=1000),
    max_candles: int = Query(default=10_000, ge=1, le=10_000),
    closed_only: bool = Query(default=False),
    adapter: MarketDataProvider = Depends(get_market_data_provider),  # noqa: B008
):
    current_user(request)
    try:
        normalized_symbol = str(Symbol(symbol))
        normalized_interval = Timeframe(interval).value
        if start_time.tzinfo is None or start_time.utcoffset() is None:
            raise ValueError("start_time must include a timezone")
        if end_time.tzinfo is None or end_time.utcoffset() is None:
            raise ValueError("end_time must include a timezone")
        if start_time > end_time:
            raise ValueError("start_time must be <= end_time")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    cache_key = "|".join(
        (
            "history",
            adapter.name,
            normalized_symbol,
            normalized_interval,
            start_time.isoformat(),
            end_time.isoformat(),
            str(page_limit),
            str(max_candles),
        )
    )
    async def load_history() -> tuple[Candle, ...]:
        try:
            fetched = await adapter.fetch_historical_ohlcv(
                normalized_symbol,
                normalized_interval,
                start_time=start_time,
                end_time=end_time,
                page_limit=page_limit,
                max_candles=max_candles,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except MarketDataProviderError as exc:
            raise HTTPException(
                status_code=502,
                detail="Market data provider unavailable or invalid",
            ) from exc
        return tuple(fetched)

    values = await _candle_cache.get_or_create(cache_key, load_history)

    output_values = tuple(candle for candle in values if candle.is_closed) if closed_only else values
    return {
        "provider": adapter.name,
        "symbol": normalized_symbol,
        "interval": normalized_interval,
        "count": len(output_values),
        "candles": [candle.model_dump(mode="json") for candle in output_values],
    }
