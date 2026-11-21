from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import BigInteger, Integer, case, cast, delete, func, select
from sqlalchemy.dialects.postgresql import insert

from app.database import get_session_factory
from app.market_data.liquidation_models import LiquidationEventRecord
from app.market_data.liquidation_stream import LiquidationEvent


async def save_liquidation_event(event: LiquidationEvent) -> bool | None:
    """Persist once; None means persistence is not configured."""
    factory = get_session_factory()
    if factory is None:
        return None
    statement = (
        insert(LiquidationEventRecord)
        .values(
            event_id=event.event_id,
            provider=event.provider,
            symbol=event.symbol,
            event_time=event.event_time,
            liquidation_side=event.liquidation_side,
            order_side=event.order_side,
            quantity=event.quantity,
            price=event.price,
            notional=event.notional,
            price_kind=event.price_kind,
        )
        .on_conflict_do_nothing(index_elements=[LiquidationEventRecord.event_id])
    )
    async with factory() as session:
        result = await session.execute(statement)
        await session.commit()
        return bool(result.rowcount)


async def list_recent_liquidation_events(
    provider: str, symbol: str, limit: int = 100
) -> list[LiquidationEvent] | None:
    """Return newest durable events, or None when PostgreSQL is not configured."""
    factory = get_session_factory()
    if factory is None:
        return None
    statement = (
        select(LiquidationEventRecord)
        .where(
            LiquidationEventRecord.provider == provider,
            LiquidationEventRecord.symbol == symbol,
        )
        .order_by(LiquidationEventRecord.event_time.desc(), LiquidationEventRecord.event_id.desc())
        .limit(limit)
    )
    async with factory() as session:
        rows = (await session.scalars(statement)).all()
    return [
        LiquidationEvent(
            event_id=row.event_id,
            provider=row.provider,
            symbol=row.symbol,
            event_time=row.event_time,
            liquidation_side=row.liquidation_side,
            order_side=row.order_side,
            quantity=row.quantity,
            price=row.price,
            notional=row.notional,
            price_kind=row.price_kind,
        )
        for row in rows
    ]


async def aggregate_liquidation_events(
    symbol: str,
    start_time: datetime,
    bucket_seconds: int,
    price_bins: int,
    provider: str | None = None,
    liquidation_side: str | None = None,
    limit: int = 20000,
) -> dict | None:
    """Aggregate a bounded recent event window inside PostgreSQL.

    Returns None when persistence is not configured. Only grouped cells cross
    the database boundary; raw event rows are never materialized for a heatmap.
    """
    if bucket_seconds < 1 or price_bins < 1 or limit < 1:
        raise ValueError("bucket_seconds, price_bins and limit must be positive")
    factory = get_session_factory()
    if factory is None:
        return None

    filters = [
        LiquidationEventRecord.symbol == symbol,
        LiquidationEventRecord.event_time >= start_time,
    ]
    if provider is not None:
        filters.append(LiquidationEventRecord.provider == provider)
    if liquidation_side is not None:
        filters.append(LiquidationEventRecord.liquidation_side == liquidation_side)

    async with factory() as session:
        total_count = int(
            await session.scalar(
                select(func.count()).select_from(LiquidationEventRecord).where(*filters)
            )
            or 0
        )
        if total_count == 0:
            return {
                "total_events": 0,
                "truncated": False,
                "price_min": None,
                "price_max": None,
                "price_step": None,
                "providers": [],
                "total_notional": "0",
                "cells": [],
            }

        bounded = (
            select(
                LiquidationEventRecord.provider.label("provider"),
                LiquidationEventRecord.event_time.label("event_time"),
                LiquidationEventRecord.liquidation_side.label("liquidation_side"),
                LiquidationEventRecord.price.label("price"),
                LiquidationEventRecord.notional.label("notional"),
            )
            .where(*filters)
            .order_by(
                LiquidationEventRecord.event_time.desc(),
                LiquidationEventRecord.event_id.desc(),
            )
            .limit(limit)
            .subquery()
        )
        min_price, max_price = (
            await session.execute(
                select(func.min(bounded.c.price), func.max(bounded.c.price))
            )
        ).one()
        price_step = (max_price - min_price) / price_bins
        if price_step <= 0:
            price_step = max(abs(min_price) * Decimal("0.001"), Decimal("0.01"))

        price_index = cast(
            func.floor((bounded.c.price - min_price) / price_step), Integer
        )
        price_index = func.least(func.greatest(price_index, 0), price_bins - 1)
        bucket_epoch = (
            cast(
                func.floor(func.extract("epoch", bounded.c.event_time) / bucket_seconds),
                BigInteger,
            )
            * bucket_seconds
        )
        time_bucket = func.to_timestamp(bucket_epoch)
        statement = (
            select(
                time_bucket.label("time_bucket"),
                price_index.label("price_bin"),
                func.count().label("events"),
                func.sum(case((bounded.c.liquidation_side == "long", 1), else_=0)).label(
                    "long_events"
                ),
                func.sum(case((bounded.c.liquidation_side == "short", 1), else_=0)).label(
                    "short_events"
                ),
                func.sum(
                    case((bounded.c.liquidation_side == "long", bounded.c.notional), else_=0)
                ).label("long_notional"),
                func.sum(
                    case((bounded.c.liquidation_side == "short", bounded.c.notional), else_=0)
                ).label("short_notional"),
                func.sum(bounded.c.notional).label("total_notional"),
            )
            .group_by(time_bucket, price_index)
            .order_by(time_bucket, price_index)
        )
        grouped = (await session.execute(statement)).mappings().all()
        providers = (
            await session.scalars(select(bounded.c.provider).distinct().order_by(bounded.c.provider))
        ).all()

    cells = [
        {
            "time_bucket": row["time_bucket"].isoformat(),
            "price_bin": row["price_bin"],
            "price_from": str(min_price + price_step * row["price_bin"]),
            "price_to": str(min_price + price_step * (row["price_bin"] + 1)),
            "events": int(row["events"]),
            "long_events": int(row["long_events"]),
            "short_events": int(row["short_events"]),
            "long_notional": str(row["long_notional"]),
            "short_notional": str(row["short_notional"]),
            "total_notional": str(row["total_notional"]),
        }
        for row in grouped
    ]
    return {
        "total_events": min(total_count, limit),
        "truncated": total_count > limit,
        "price_min": str(min_price),
        "price_max": str(max_price),
        "price_step": str(price_step),
        "providers": list(providers),
        "total_notional": str(sum((Decimal(cell["total_notional"]) for cell in cells), Decimal(0))),
        "cells": cells,
    }


async def delete_expired_liquidation_events(retention_days: int) -> int | None:
    """Delete persisted events older than the configured retention window.

    None means PostgreSQL persistence is not configured. This is deliberately
    an explicit maintenance operation; ingestion never deletes historical data.
    """
    if retention_days < 1:
        raise ValueError("retention_days must be at least 1")
    factory = get_session_factory()
    if factory is None:
        return None
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    async with factory() as session:
        result = await session.execute(
            delete(LiquidationEventRecord).where(LiquidationEventRecord.event_time < cutoff)
        )
        await session.commit()
        return int(result.rowcount or 0)
