from __future__ import annotations

import asyncio

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
from app.market_data.multi_timeframe import build_multi_timeframe_confluence
from app.market_data.symbol import Symbol

router = APIRouter(prefix="/api/v1/derivatives", tags=["derivatives"])
_cache: AsyncTTLCache = AsyncTTLCache(ttl_seconds=30, max_entries=128)
_PERIODS = ("5m", "15m", "1h", "4h")


@router.get("/confluence")
async def multi_timeframe_confluence(
    request: Request,
    symbol: str = Query(min_length=2, max_length=30),
    limit: int = Query(default=200, ge=20, le=200),
    pivot_window: int = Query(default=3, ge=2, le=10),
):
    """Compare closed Mark Price market structure across four fixed timeframes."""
    current_user(request)
    try:
        normalized = str(Symbol(symbol))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    configured = get_futures_provider()
    if configured.name == "binance_usdm_futures":
        adapter = BinanceUsdmMarkPriceAdapter()
    elif configured.name == "bybit_linear_futures":
        adapter = BybitLinearMarkPriceAdapter()
    else:
        raise HTTPException(status_code=503, detail="No mark price provider configured")

    key = f"{adapter.name}|{normalized}|{limit}|{pivot_window}|{'-'.join(_PERIODS)}"

    async def load():
        async def analyze_period(period: str):
            history = await adapter.fetch_mark_price_history(normalized, period, limit)
            return analyze_market_structure(
                symbol=normalized,
                period=period,
                provider=history.provider,
                candles=history.candles,
                observed_at=history.observed_at,
                pivot_window=pivot_window,
            )

        results = await asyncio.gather(
            *(analyze_period(period) for period in _PERIODS),
            return_exceptions=True,
        )
        analyses = []
        errors = []
        for period, result in zip(_PERIODS, results, strict=True):
            if isinstance(result, BaseException):
                errors.append(period)
                continue
            analyses.append(result)
        if not analyses:
            raise HTTPException(
                status_code=502,
                detail="All multi-timeframe market-data requests failed",
            )
        observed_at = max(item.observed_at for item in analyses)
        response = build_multi_timeframe_confluence(
            symbol=normalized,
            provider=adapter.name,
            analyses=analyses,
            observed_at=observed_at,
        ).model_dump(mode="json")
        response["unavailable_timeframes"] = errors
        if errors:
            response["caveats"].append(
                "دریافت داده برای برخی تایم‌فریم‌ها ناموفق بود؛ نتیجه همگرایی ناقص است."
            )
        return response

    try:
        return await _cache.get_or_create(key, load)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FuturesMarketDataError as exc:
        raise HTTPException(
            status_code=502,
            detail="Multi-timeframe market-data provider unavailable or returned invalid data",
        ) from exc
