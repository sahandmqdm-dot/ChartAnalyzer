import asyncio

from fastapi import APIRouter, HTTPException, Query, Request

from app.auth import current_user
from app.market_data.futures import FuturesMarketDataError
from app.market_data.futures_analysis import analyze_futures_context
from app.market_data.futures_history import (
    BinanceUsdmFuturesHistoryAdapter,
    BybitLinearFuturesHistoryAdapter,
)
from app.market_data.futures_routes import get_futures_provider
from app.market_data.provider import MarketDataProviderError
from app.market_data.registry import get_market_data_provider
from app.market_data.symbol import Symbol
from app.market_data.timeframe import Timeframe

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
        interval = Timeframe(period).value
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

    spot_adapter = get_market_data_provider()
    try:
        history, candles = await asyncio.gather(
            history_adapter.fetch_history(normalized, period, 200),
            spot_adapter.fetch_ohlcv(normalized, interval, limit=3),
        )
    except FuturesMarketDataError as exc:
        raise HTTPException(
            status_code=502,
            detail="Futures history provider unavailable or returned invalid data",
        ) from exc
    except MarketDataProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail="Spot price context provider unavailable or returned invalid data",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return analyze_futures_context(history, candles).model_dump(mode="json")
