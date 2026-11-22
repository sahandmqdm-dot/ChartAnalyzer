from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.database import get_session_factory
from app.market_data.liquidation_models import FuturesOpenInterestRecord


async def save_open_interest(
    provider: str,
    symbol: str,
    observed_at: datetime,
    open_interest: Decimal,
) -> bool | None:
    """Persist a public open-interest observation idempotently."""
    factory = get_session_factory()
    if factory is None:
        return None
    identity = f"{provider}|{symbol}|{observed_at.isoformat()}"
    observation_id = hashlib.sha256(identity.encode()).hexdigest()
    statement = (
        insert(FuturesOpenInterestRecord)
        .values(
            observation_id=observation_id,
            provider=provider,
            symbol=symbol,
            observed_at=observed_at,
            open_interest=open_interest,
        )
        .on_conflict_do_nothing(index_elements=[FuturesOpenInterestRecord.observation_id])
    )
    async with factory() as session:
        result = await session.execute(statement)
        await session.commit()
        return bool(result.rowcount)


async def list_open_interest(
    symbol: str,
    start_time: datetime,
    limit: int = 1000,
    provider: str = "binance_usdm_futures",
) -> list[dict] | None:
    if not 1 <= limit <= 5000:
        raise ValueError("limit must be between 1 and 5000")
    factory = get_session_factory()
    if factory is None:
        return None
    statement = (
        select(FuturesOpenInterestRecord)
        .where(
            FuturesOpenInterestRecord.provider == provider,
            FuturesOpenInterestRecord.symbol == symbol,
            FuturesOpenInterestRecord.observed_at >= start_time,
        )
        .order_by(FuturesOpenInterestRecord.observed_at.desc())
        .limit(limit)
    )
    async with factory() as session:
        rows = (await session.scalars(statement)).all()
    return [
        {
            "provider": row.provider,
            "symbol": row.symbol,
            "observed_at": row.observed_at.isoformat(),
            "open_interest": str(row.open_interest),
        }
        for row in reversed(rows)
    ]
