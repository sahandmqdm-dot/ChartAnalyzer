from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from app.auth import current_user
from app.market_data.futures_routes import get_futures_provider
from app.market_data.liquidation_repository import (
    aggregate_liquidation_events,
    list_recent_liquidation_events,
)
from app.market_data.liquidation_stream import get_collector
from app.market_data.symbol import Symbol

router = APIRouter(prefix="/api/v1/derivatives", tags=["derivatives"])


def _collector_for(symbol: str):
    provider = get_futures_provider()
    if provider.name not in {"binance_usdm_futures", "bybit_linear_futures"}:
        raise HTTPException(status_code=503, detail="Liquidation stream is not configured")
    return get_collector(provider.name, symbol)


@router.post("/liquidations/start")
async def start_liquidation_stream(
    request: Request, symbol: str = Query(min_length=2, max_length=30)
):
    """Start a public exchange liquidation stream for one linear perpetual symbol."""
    current_user(request)
    try:
        normalized = str(Symbol(symbol))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    collector = _collector_for(normalized)
    collector.start()
    return collector.snapshot()


@router.get("/liquidations")
async def liquidation_snapshot(
    request: Request,
    symbol: str = Query(min_length=2, max_length=30),
    limit: int = Query(default=100, ge=1, le=500),
):
    """Return durable recent events when PostgreSQL is available, otherwise the memory snapshot."""
    current_user(request)
    try:
        normalized = str(Symbol(symbol))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    collector = _collector_for(normalized)
    snapshot = collector.snapshot(limit)
    try:
        durable_events = await list_recent_liquidation_events(collector.provider, normalized, limit)
        if durable_events is not None:
            snapshot["events"] = [event.model_dump(mode="json") for event in durable_events]
            snapshot["status"]["event_count"] = len(durable_events)
            snapshot["status"]["last_event_time"] = (
                durable_events[0].event_time.isoformat() if durable_events else None
            )
            snapshot["retention"] = "postgresql_persistent"
            snapshot["storage_status"] = "available"
        else:
            snapshot["storage_status"] = "not_configured"
    except Exception:  # noqa: BLE001 - serve bounded memory snapshot if storage is down
        snapshot["storage_status"] = "unavailable_using_memory_fallback"
    return snapshot


@router.delete("/liquidations/stop")
async def stop_liquidation_stream(
    request: Request, symbol: str = Query(min_length=2, max_length=30)
):
    """Stop the selected public stream and clear its in-memory event buffer."""
    current_user(request)
    try:
        normalized = str(Symbol(symbol))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    collector = _collector_for(normalized)
    await collector.stop()
    collector.clear()
    return collector.snapshot()



_WINDOW_HOURS = {"1h": 1, "6h": 6, "24h": 24, "3d": 72, "7d": 168}


@router.get("/liquidations/heatmap")
async def liquidation_heatmap(
    request: Request,
    symbol: str = Query(min_length=2, max_length=30),
    window: Literal["1h", "6h", "24h", "3d", "7d"] = "24h",
    bucket_minutes: Literal[1, 5, 15, 60] = 5,
    price_bins: int = Query(default=24, ge=8, le=60),
    provider: Literal["all", "binance_usdm_futures", "bybit_linear_futures"] = "all",
    side: Literal["all", "long", "short"] = "all",
    limit: int = Query(default=20000, ge=100, le=50000),
):
    """Aggregate observed liquidation events from PostgreSQL into time/price cells."""
    current_user(request)
    try:
        normalized = str(Symbol(symbol))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    end_time = datetime.now(UTC)
    start_time = end_time - timedelta(hours=_WINDOW_HOURS[window])
    provider_filter = None if provider == "all" else provider
    try:
        aggregation = await aggregate_liquidation_events(
            normalized,
            start_time,
            bucket_minutes * 60,
            price_bins,
            provider_filter,
            None if side == "all" else side,
            limit,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Liquidation history storage is unavailable") from exc
    if aggregation is None:
        raise HTTPException(status_code=503, detail="PostgreSQL persistence is not configured")

    return {
        "symbol": normalized,
        "window": window,
        "bucket_minutes": bucket_minutes,
        "price_bins": price_bins,
        "provider_filter": provider,
        "side_filter": side,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        **aggregation,
        "data_semantics": (
            "Observed exchange liquidation stream events only; not estimated future liquidation "
            "levels or a complete exchange-wide liquidation tape."
        ),
    }
