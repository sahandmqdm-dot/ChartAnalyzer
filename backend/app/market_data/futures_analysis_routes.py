import asyncio
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request

from app.auth import current_user
from app.market_data.futures import FuturesMarketDataError
from app.market_data.futures_analysis import analyze_futures_context
from app.market_data.futures_history import (
    BinanceUsdmFuturesHistoryAdapter,
    BybitLinearFuturesHistoryAdapter,
)
from app.market_data.futures_routes import get_futures_provider
from app.market_data.liquidation_repository import list_recent_liquidation_events
from app.market_data.mark_price import (
    BinanceUsdmMarkPriceAdapter,
    BybitLinearMarkPriceAdapter,
)
from app.market_data.market_pressure import assess_market_pressure
from app.market_data.symbol import Symbol

router = APIRouter(prefix="/api/v1/derivatives", tags=["derivatives"])
_PERIODS = {"5m", "15m", "30m", "1h", "4h", "1d"}


@router.get("/analysis")
async def futures_analysis(
    request: Request,
    symbol: str = Query(min_length=2, max_length=30),
    period: str = Query(default="1h"),
):
    """Return descriptive futures positioning context; never a trade instruction."""
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
        history_adapter = BinanceUsdmFuturesHistoryAdapter()
    elif configured.name == "bybit_linear_futures":
        history_adapter = BybitLinearFuturesHistoryAdapter()
    else:
        raise HTTPException(status_code=503, detail="No historical futures provider configured")

    if configured.name == "binance_usdm_futures":
        price_adapter = BinanceUsdmMarkPriceAdapter()
    else:
        price_adapter = BybitLinearMarkPriceAdapter()
    try:
        history, mark_price = await asyncio.gather(
            history_adapter.fetch_history(normalized, period, 200),
            price_adapter.fetch_mark_price_history(normalized, period, 3),
        )
    except FuturesMarketDataError as exc:
        raise HTTPException(
            status_code=502,
            detail="Futures history provider unavailable or returned invalid data",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return analyze_futures_context(history, mark_price.candles).model_dump(mode="json")



@router.get("/market-pressure")
async def futures_market_pressure(
    request: Request,
    symbol: str = Query(min_length=2, max_length=30),
    period: str = Query(default="1h"),
    liquidation_limit: int = Query(default=200, ge=1, le=500),
):
    """Combine public derivatives context with observed liquidation events."""
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
        history_adapter = BinanceUsdmFuturesHistoryAdapter()
        price_adapter = BinanceUsdmMarkPriceAdapter()
    elif configured.name == "bybit_linear_futures":
        history_adapter = BybitLinearFuturesHistoryAdapter()
        price_adapter = BybitLinearMarkPriceAdapter()
    else:
        raise HTTPException(status_code=503, detail="No historical futures provider configured")

    try:
        history, mark_price = await asyncio.gather(
            history_adapter.fetch_history(normalized, period, 200),
            price_adapter.fetch_mark_price_history(normalized, period, 3),
        )
    except FuturesMarketDataError as exc:
        raise HTTPException(
            status_code=502,
            detail="Futures history provider unavailable or returned invalid data",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    analysis = analyze_futures_context(history, mark_price.candles)
    try:
        events = await list_recent_liquidation_events(
            configured.name, normalized, liquidation_limit
        )
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail="Liquidation history storage is unavailable"
        ) from exc
    period_minutes = {"5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1440}[period]
    cutoff = datetime.now(UTC) - timedelta(minutes=period_minutes)
    window_events = [event for event in (events or []) if event.event_time >= cutoff]
    assessment = assess_market_pressure(analysis, window_events)
    result = assessment.model_dump(mode="json")
    result["liquidation_data_state"] = (
        "available" if events is not None and window_events else
        "no_observed_events" if events is not None else "persistence_not_configured"
    )
    result["provider"] = configured.name
    result["period"] = period
    return result
