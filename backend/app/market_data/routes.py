from datetime import datetime
from fastapi import APIRouter, HTTPException, Query

from app.auth import current_user
from app.market_data.binance import BinanceSpotAdapter, MarketDataProviderError
from fastapi import Request

router = APIRouter(prefix="/api/v1/market-data", tags=["market-data"])


@router.get("/candles")
async def candles(
    request: Request,
    symbol: str = Query(min_length=2, max_length=30),
    interval: str = Query(default="1h", max_length=5),
    limit: int = Query(default=500, ge=1, le=1000),
    start_time: datetime | None = None,
    end_time: datetime | None = None,
):
    current_user(request)
    adapter = BinanceSpotAdapter()
    try:
        values = await adapter.fetch_ohlcv(
            symbol,
            interval,
            limit=limit,
            start_time=start_time,
            end_time=end_time,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MarketDataProviderError as exc:
        raise HTTPException(status_code=502, detail="Market data provider unavailable or invalid") from exc
    return {
        "provider": "binance_spot",
        "symbol": symbol.strip().upper(),
        "interval": interval,
        "count": len(values),
        "candles": [candle.model_dump(mode="json") for candle in values],
    }
