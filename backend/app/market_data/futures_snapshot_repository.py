from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.database import get_session_factory
from app.market_data.liquidation_models import FuturesMarkSnapshotRecord


async def save_futures_mark_snapshot(
    provider: str,
    symbol: str,
    observed_at: datetime,
    mark_price: Decimal,
    index_price: Decimal,
    funding_rate: Decimal,
    next_funding_time: datetime,
) -> bool | None:
    """Persist one public mark-price update idempotently; None means DB is disabled."""
    factory = get_session_factory()
    if factory is None:
        return None
    identity = "|".join((provider, symbol, observed_at.isoformat()))
    snapshot_id = hashlib.sha256(identity.encode()).hexdigest()
    statement = (
        insert(FuturesMarkSnapshotRecord)
        .values(
            snapshot_id=snapshot_id,
            provider=provider,
            symbol=symbol,
            observed_at=observed_at,
            mark_price=mark_price,
            index_price=index_price,
            funding_rate=funding_rate,
            next_funding_time=next_funding_time,
        )
        .on_conflict_do_nothing(index_elements=[FuturesMarkSnapshotRecord.snapshot_id])
    )
    async with factory() as session:
        result = await session.execute(statement)
        await session.commit()
        return bool(result.rowcount)


async def list_futures_mark_snapshots(
    symbol: str,
    start_time: datetime,
    limit: int = 1000,
    provider: str | None = None,
) -> list[dict] | None:
    """Return a bounded chronological history of persisted mark/funding observations."""
    if not 1 <= limit <= 5000:
        raise ValueError("limit must be between 1 and 5000")
    factory = get_session_factory()
    if factory is None:
        return None
    filters = [
        FuturesMarkSnapshotRecord.symbol == symbol,
        FuturesMarkSnapshotRecord.observed_at >= start_time,
    ]
    if provider is not None:
        filters.append(FuturesMarkSnapshotRecord.provider == provider)
    statement = (
        select(FuturesMarkSnapshotRecord)
        .where(*filters)
        .order_by(FuturesMarkSnapshotRecord.observed_at.desc())
        .limit(limit)
    )
    async with factory() as session:
        rows = (await session.scalars(statement)).all()
    return [
        {
            "provider": row.provider,
            "symbol": row.symbol,
            "observed_at": row.observed_at.isoformat(),
            "mark_price": str(row.mark_price),
            "index_price": str(row.index_price),
            "funding_rate": str(row.funding_rate),
            "next_funding_time": row.next_funding_time.isoformat(),
        }
        for row in reversed(rows)
    ]
