from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request

from app.auth import csrf_check, current_user
from app.market_data.futures_routes import get_futures_provider
from app.market_data.liquidation_repository import (
    aggregate_liquidation_events,
    aggregate_liquidation_price_bands,
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
    csrf_check(request)
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
    csrf_check(request)
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


@router.get("/liquidations/clusters")
async def liquidation_clusters(
    request: Request,
    symbol: str = Query(min_length=2, max_length=30),
    window: Literal["1h", "6h", "24h", "3d", "7d"] = "24h",
    price_bins: int = Query(default=40, ge=8, le=100),
    provider: Literal["all", "binance_usdm_futures", "bybit_linear_futures"] = "all",
    side: Literal["all", "long", "short"] = "all",
    min_strength_pct: int = Query(default=35, ge=1, le=100),
    min_events: int = Query(default=2, ge=1, le=1000),
    include_market_context: bool = False,
    limit: int = Query(default=20000, ge=100, le=50000),
):
    """Detect contiguous price bands with elevated observed liquidation notional."""
    current_user(request)
    try:
        normalized = str(Symbol(symbol))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    end_time = datetime.now(UTC)
    start_time = end_time - timedelta(hours=_WINDOW_HOURS[window])
    provider_filter = None if provider == "all" else provider
    try:
        aggregation = await aggregate_liquidation_price_bands(
            normalized,
            start_time,
            price_bins,
            provider_filter,
            None if side == "all" else side,
            limit,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Liquidation history storage is unavailable") from exc
    if aggregation is None:
        raise HTTPException(status_code=503, detail="PostgreSQL persistence is not configured")

    bands = aggregation["bands"]
    max_notional = max(
        (Decimal(band["total_notional"]) for band in bands),
        default=Decimal(0),
    )
    threshold = max_notional * Decimal(min_strength_pct) / Decimal(100)
    active = [
        band
        for band in bands
        if Decimal(band["total_notional"]) >= threshold
        and band["events"] >= min_events
    ]
    clusters = []
    current = []
    for band in active:
        if current and band["price_bin"] != current[-1]["price_bin"] + 1:
            clusters.append(current)
            current = []
        current.append(band)
    if current:
        clusters.append(current)

    results = []
    for group in clusters:
        long_notional = sum(
            (Decimal(item["long_notional"]) for item in group), Decimal(0)
        )
        short_notional = sum(
            (Decimal(item["short_notional"]) for item in group), Decimal(0)
        )
        total_notional = long_notional + short_notional
        event_count = sum(item["events"] for item in group)
        results.append(
            {
                "price_from": group[0]["price_from"],
                "price_to": group[-1]["price_to"],
                "mid_price": str(
                    (Decimal(group[0]["price_from"]) + Decimal(group[-1]["price_to"]))
                    / Decimal(2)
                ),
                "price_bins": len(group),
                "events": event_count,
                "long_events": sum(item["long_events"] for item in group),
                "short_events": sum(item["short_events"] for item in group),
                "long_notional": str(long_notional),
                "short_notional": str(short_notional),
                "total_notional": str(total_notional),
                "strength_pct": str(
                    (total_notional / max_notional * Decimal(100))
                    if max_notional
                    else Decimal(0)
                ),
                "dominant_side": (
                    "long" if long_notional > short_notional
                    else "short" if short_notional > long_notional
                    else "balanced"
                ),
                "last_event_time": max(item["last_event_time"] for item in group),
            }
        )
    results.sort(key=lambda cluster: Decimal(cluster["total_notional"]), reverse=True)

    market_context = {
        "status": "not_requested",
        "note": "Optional futures history context; descriptive only and not causal."
    }
    if include_market_context:
        try:
            from app.market_data.futures_history import (
                BinanceUsdmFuturesHistoryAdapter,
                BybitLinearFuturesHistoryAdapter,
            )

            configured = get_futures_provider()
            history_adapter = (
                BinanceUsdmFuturesHistoryAdapter()
                if configured.name == "binance_usdm_futures"
                else BybitLinearFuturesHistoryAdapter()
            )
            history = await history_adapter.fetch_history(normalized, "1h", 2)
            oi_points = history.open_interest
            oi_change = None
            if len(oi_points) >= 2 and oi_points[-2].value != 0:
                oi_change = (
                    (oi_points[-1].value - oi_points[-2].value)
                    / oi_points[-2].value
                    * Decimal(100)
                )
            market_context = {
                "status": "available",
                "provider": history.provider,
                "funding_rate": str(history.funding_rate[-1].value)
                if history.funding_rate else None,
                "funding_timestamp": history.funding_rate[-1].timestamp.isoformat()
                if history.funding_rate else None,
                "open_interest": str(oi_points[-1].value) if oi_points else None,
                "open_interest_change_pct": str(oi_change) if oi_change is not None else None,
                "open_interest_timestamp": oi_points[-1].timestamp.isoformat()
                if oi_points else None,
                "long_short_ratio": str(history.long_short_ratio[-1].value)
                if history.long_short_ratio else None,
                "observed_at": history.observed_at.isoformat(),
                "note": "Context is sourced from the configured futures venue and is not linked causally to observed liquidation clusters."
            }
        except Exception:  # noqa: BLE001 - context is optional and must not break observed-event analysis
            market_context = {
                "status": "unavailable",
                "note": "Futures history could not be retrieved; liquidation clusters remain based on persisted observed events."
            }

    return {
        "symbol": normalized,
        "window": window,
        "provider_filter": provider,
        "side_filter": side,
        "min_strength_pct": min_strength_pct,
        "min_events_per_band": min_events,
        "total_events": aggregation["total_events"],
        "truncated": aggregation["truncated"],
        "price_min": aggregation["price_min"],
        "price_max": aggregation["price_max"],
        "price_step": aggregation["price_step"],
        "total_notional": aggregation["total_notional"],
        "clusters": results,
        "method": (
            "Contiguous price bins are selected when each bin reaches the configured percentage "
            "of the strongest single-bin notional and minimum event count. This is a descriptive "
            "heuristic over observed feed events, not estimated open positions or future liquidation levels."
        ),
        "market_context": market_context,
    }
