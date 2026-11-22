import asyncio
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.auth import current_user
from app.backtest.engine import run_sma_crossover
from app.backtest.futures_engine import FundingPayment, run_futures_sma_crossover
from app.market_data.provider import MarketDataProvider, MarketDataProviderError
from app.market_data.futures import FuturesMarketDataError
from app.market_data.futures_history import BinanceUsdmFuturesHistoryAdapter, BybitLinearFuturesHistoryAdapter
from app.market_data.mark_price import BinanceUsdmMarkPriceAdapter, BybitLinearMarkPriceAdapter
from app.market_data.futures_routes import get_futures_provider
from app.market_data.registry import get_market_data_provider
from app.market_data.symbol import Symbol
from app.market_data.timeframe import Timeframe

router = APIRouter(prefix="/api/v1/backtest", tags=["backtest"])

SUPPORTED_FUTURES_BACKTEST_INTERVALS = {"5m", "15m", "30m", "1h", "4h", "1d"}


@router.get("/sma-crossover")
async def sma_crossover(
    request: Request,
    symbol: str = Query(min_length=2, max_length=30),
    interval: str = Query(default="1h", max_length=5),
    start_time: datetime = Query(...),  # noqa: B008
    end_time: datetime = Query(...),  # noqa: B008
    fast_period: int = Query(default=10, ge=1, le=500),
    slow_period: int = Query(default=30, ge=2, le=1000),
    initial_equity: Decimal = Query(default=Decimal(10000), gt=0),  # noqa: B008
    fee_rate: Decimal = Query(default=Decimal("0.001"), ge=0, lt=1),  # noqa: B008
    slippage_rate: Decimal = Query(default=Decimal("0.0005"), ge=0, lt=1),  # noqa: B008
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


@router.get("/futures-sma-crossover")
async def futures_sma_crossover(
    request: Request,
    symbol: str = Query(min_length=2, max_length=30),
    interval: str = Query(default="1h", max_length=5),
    limit: int = Query(default=200, ge=32, le=200),
    fast_period: int = Query(default=10, ge=1, le=100),
    slow_period: int = Query(default=30, ge=2, le=150),
    initial_equity: Decimal = Query(default=Decimal("10000"), gt=0),
    leverage: Decimal = Query(default=Decimal("3"), ge=1, le=125),
    margin_fraction: Decimal = Query(default=Decimal("0.25"), gt=0, le=1),
    fee_rate: Decimal = Query(default=Decimal("0.0004"), ge=0, lt=1),
    slippage_rate: Decimal = Query(default=Decimal("0.0002"), ge=0, lt=1),
    maintenance_margin_rate: Decimal = Query(default=Decimal("0.005"), ge=0, lt=1),
    liquidation_fee_rate: Decimal = Query(default=Decimal("0.01"), ge=0, lt=1),
):
    """Run an authenticated read-only linear-perpetual SMA backtest."""
    current_user(request)
    try:
        normalized_symbol = str(Symbol(symbol))
        normalized_interval = Timeframe(interval).value
        if normalized_interval not in SUPPORTED_FUTURES_BACKTEST_INTERVALS:
            raise ValueError(
                "futures backtest interval must be one of: "
                + ", ".join(sorted(SUPPORTED_FUTURES_BACKTEST_INTERVALS))
            )
        if fast_period >= slow_period:
            raise ValueError("periods must satisfy 1 <= fast_period < slow_period")
        spot_provider = get_market_data_provider()
        futures_provider = get_futures_provider()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Market data provider is not configured") from exc

    if futures_provider.name == "binance_usdm_futures":
        mark_adapter = BinanceUsdmMarkPriceAdapter()
        history_adapter = BinanceUsdmFuturesHistoryAdapter()
    elif futures_provider.name == "bybit_linear_futures":
        mark_adapter = BybitLinearMarkPriceAdapter()
        history_adapter = BybitLinearFuturesHistoryAdapter()
    else:
        raise HTTPException(status_code=503, detail="Unsupported futures market-data provider")

    try:
        spot_candles, mark_history, futures_history = await asyncio.gather(
            spot_provider.fetch_ohlcv(normalized_symbol, normalized_interval, limit=limit),
            mark_adapter.fetch_mark_price_history(normalized_symbol, normalized_interval, limit),
            history_adapter.fetch_history(normalized_symbol, normalized_interval, limit),
        )
    except (MarketDataProviderError, FuturesMarketDataError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="Futures backtest market data unavailable or invalid") from exc

    marks_by_time = {item.timestamp: item for item in mark_history.candles if item.is_closed}
    aligned_spot = [item for item in spot_candles if item.is_closed and item.timestamp in marks_by_time]
    aligned_marks = [marks_by_time[item.timestamp] for item in aligned_spot]
    if len(aligned_spot) < slow_period + 2:
        raise HTTPException(
            status_code=422,
            detail="Not enough aligned closed OHLCV and Mark Price candles for the selected periods",
        )

    funding = [
        FundingPayment(timestamp=point.timestamp, rate=point.value)
        for point in futures_history.funding_rate
        if aligned_spot[0].timestamp < point.timestamp <= (aligned_spot[-1].close_time or aligned_spot[-1].timestamp)
    ]
    try:
        result = run_futures_sma_crossover(
            aligned_spot,
            aligned_marks,
            fast_period=fast_period,
            slow_period=slow_period,
            initial_equity=initial_equity,
            leverage=leverage,
            margin_fraction=margin_fraction,
            fee_rate=fee_rate,
            slippage_rate=slippage_rate,
            maintenance_margin_rate=maintenance_margin_rate,
            liquidation_fee_rate=liquidation_fee_rate,
            funding_payments=funding,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "provider": futures_provider.name,
        "symbol": normalized_symbol,
        "interval": normalized_interval,
        "strategy": "futures_sma_crossover",
        "candles_used": len(aligned_spot),
        "funding_events_used": len(funding),
        "parameters": {
            "fast_period": fast_period,
            "slow_period": slow_period,
            "initial_equity": str(initial_equity),
            "leverage": str(leverage),
            "margin_fraction": str(margin_fraction),
            "fee_rate": str(fee_rate),
            "slippage_rate": str(slippage_rate),
            "maintenance_margin_rate": str(maintenance_margin_rate),
            "liquidation_fee_rate": str(liquidation_fee_rate),
        },
        "result": result.to_dict(),
        "model_limitations": [
            "Spot OHLCV currently drives signals and simulated entry/exit prices; actual futures-contract OHLCV and order-book execution are not fetched, so basis and execution differences are omitted.",
            "Funding history is limited by the selected provider's returned history window; event cashflows use the event candle's Mark Price close as an approximation.",
            "Funding cashflows update wallet balance, but the simplified liquidation threshold is not dynamically recalculated from cumulative funding; liquidation timing can therefore differ from an exchange.",
            "Liquidation uses a simplified isolated-margin estimate and candle OHLC approximation; exchange margin tiers, insurance funds, and auto-deleveraging are not modeled.",
            "Execution is modeled at next-candle open with fixed slippage; exchange order-book fills are not modeled.",
            "Maximum drawdown is sampled at candle closes and can understate intrabar equity drawdown.",
        ],
    }
