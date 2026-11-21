from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import current_user
from app.backtest.engine import run_sma_crossover
from app.market_data.provider import MarketDataProvider, MarketDataProviderError
from app.market_data.registry import get_market_data_provider
from app.market_data.symbol import Symbol
from app.market_data.timeframe import Timeframe

router = APIRouter(prefix="/api/v1/backtest", tags=["backtest"])


@router.get("/sma-crossover")
async def sma_crossover(
    request: Request,
    symbol: str = Query(min_length=2, max_length=30),
    interval: str = Query(default="1h", max_length=5),
    start_time: datetime = Query(...),
    end_time: datetime = Query(...),
    fast_period: int = Query(default=10, ge=1, le=500),
    slow_period: int = Query(default=30, ge=2, le=1000),
    initial_equity: Decimal = Query(default=Decimal("10000"), gt=0),
    fee_rate: Decimal = Query(default=Decimal("0.001"), ge=0, lt=1),
    slippage_rate: Decimal = Query(default=Decimal("0.0005"), ge=0, lt=1),
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
        if start_time >= end_time:
            raise ValueError("start_time must be before end_time")
        if fast_period >= slow_period:
            raise ValueError("periods must satisfy 1 <= fast_period < slow_period")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        fetched = await adapter.fetch_historical_ohlcv(
            normalized_symbol,
            normalized_interval,
            start_time=start_time,
            end_time=end_time,
            page_limit=1000,
            max_candles=10_000,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except MarketDataProviderError as exc:
        raise HTTPException(
            status_code=502,
            detail="Market data provider unavailable or invalid",
        ) from exc

    closed_candles = [candle for candle in fetched if candle.is_closed]
    try:
        result = run_sma_crossover(
            closed_candles,
            fast_period=fast_period,
            slow_period=slow_period,
            initial_equity=initial_equity,
            fee_rate=fee_rate,
            slippage_rate=slippage_rate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "provider": adapter.name,
        "symbol": normalized_symbol,
        "interval": normalized_interval,
        "strategy": "sma_crossover",
        "parameters": {
            "fast_period": fast_period,
            "slow_period": slow_period,
            "initial_equity": str(initial_equity),
            "fee_rate": str(fee_rate),
            "slippage_rate": str(slippage_rate),
        },
        "candles_used": len(closed_candles),
        "result": result.to_dict(),
    }
