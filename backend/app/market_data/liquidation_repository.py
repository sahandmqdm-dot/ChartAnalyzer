from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
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


async def list_liquidation_events_since(
    symbol: str,
    start_time: datetime,
    provider: str | None = None,
    liquidation_side: str | None = None,
    limit: int = 50001,
) -> list[LiquidationEvent] | None:
    """Return bounded durable events for aggregation, or None without PostgreSQL."""
    factory = get_session_factory()
    if factory is None:
        return None
    statement = select(LiquidationEventRecord).where(
        LiquidationEventRecord.symbol == symbol,
        LiquidationEventRecord.event_time >= start_time,
    )
    if provider is not None:
        statement = statement.where(LiquidationEventRecord.provider == provider)
    if liquidation_side is not None:
        statement = statement.where(LiquidationEventRecord.liquidation_side == liquidation_side)
    statement = statement.order_by(
        LiquidationEventRecord.event_time.desc(), LiquidationEventRecord.event_id.desc()
    ).limit(limit)
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
