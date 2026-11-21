import subprocess
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.database import get_session_factory
from app.market_data import liquidation_repository as repository
from app.market_data.liquidation_models import LiquidationEventRecord
from app.market_data.liquidation_stream import LiquidationEvent

DATABASE_URL = __import__("os").environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="PostgreSQL integration test requires DATABASE_URL")


@pytest.fixture(scope="session", autouse=True)
def apply_postgres_migrations():
    backend_dir = Path(__file__).resolve().parents[1]
    subprocess.run(
        ["alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=backend_dir,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.asyncio
async def test_postgres_migration_and_liquidation_repository_round_trip():
    event_id = uuid4().hex[:24]
    event = LiquidationEvent(
        event_id=event_id,
        provider="integration_test",
        symbol="BTCUSDT",
        event_time=datetime.now(UTC),
        liquidation_side="long",
        order_side="SELL",
        quantity=Decimal("0.125"),
        price=Decimal("91000.25"),
        notional=Decimal("11375.03125"),
        price_kind="average_execution",
    )
    factory = get_session_factory()
    assert factory is not None
    expired_id = uuid4().hex[:24]
    expired_event = event.model_copy(
        update={"event_id": expired_id, "event_time": datetime.now(UTC) - timedelta(days=120)}
    )

    try:
        assert await repository.save_liquidation_event(event) is True
        assert await repository.save_liquidation_event(event) is False
        assert await repository.save_liquidation_event(expired_event) is True

        rows = await repository.list_recent_liquidation_events(
            "integration_test", "BTCUSDT", limit=10
        )
        stored = next(row for row in rows if row.event_id == event_id)
        assert stored.quantity == Decimal("0.125")
        assert stored.price == Decimal("91000.250000000000000000")
        assert stored.notional == Decimal("11375.031250000000000000")
        assert stored.event_time == event.event_time

        assert await repository.delete_expired_liquidation_events(90) == 1
        remaining = await repository.list_recent_liquidation_events(
            "integration_test", "BTCUSDT", limit=10
        )
        assert [row.event_id for row in remaining] == [event_id]
    finally:
        async with factory() as session:
            await session.execute(
                delete(LiquidationEventRecord).where(
                    LiquidationEventRecord.event_id.in_([event_id, expired_id])
                )
            )
            await session.commit()
