from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.market_data import liquidation_repository as repository
from app.market_data.liquidation_stream import LiquidationEvent


def sample_event():
    return LiquidationEvent(
        event_id="a" * 24,
        provider="binance_usdm_futures",
        symbol="BTCUSDT",
        event_time=datetime(2026, 11, 21, tzinfo=UTC),
        liquidation_side="long",
        order_side="SELL",
        quantity=Decimal("0.125"),
        price=Decimal("91000.25"),
        notional=Decimal("11375.03125"),
        price_kind="average_execution",
    )


class FakeSession:
    def __init__(self, rowcount=1, rows=None):
        self.rowcount = rowcount
        self.rows = rows or []
        self.statement = None
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, statement):
        self.statement = statement
        return SimpleNamespace(rowcount=self.rowcount)

    async def scalars(self, _statement):
        return SimpleNamespace(all=lambda: self.rows)

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_save_event_uses_postgres_conflict_safe_insert(monkeypatch):
    session = FakeSession(rowcount=1)
    monkeypatch.setattr(repository, "get_session_factory", lambda: lambda: session)

    saved = await repository.save_liquidation_event(sample_event())

    assert saved is True
    assert session.committed is True
    sql = str(session.statement.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (event_id) DO NOTHING" in sql


@pytest.mark.asyncio
async def test_save_duplicate_event_is_idempotent(monkeypatch):
    session = FakeSession(rowcount=0)
    monkeypatch.setattr(repository, "get_session_factory", lambda: lambda: session)

    assert await repository.save_liquidation_event(sample_event()) is False


@pytest.mark.asyncio
async def test_repository_returns_none_when_database_not_configured(monkeypatch):
    monkeypatch.setattr(repository, "get_session_factory", lambda: None)

    assert await repository.save_liquidation_event(sample_event()) is None
    assert await repository.list_recent_liquidation_events("binance_usdm_futures", "BTCUSDT") is None


@pytest.mark.asyncio
async def test_list_recent_events_rehydrates_decimal_domain_objects(monkeypatch):
    event = sample_event()
    record = SimpleNamespace(
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
    session = FakeSession(rows=[record])
    monkeypatch.setattr(repository, "get_session_factory", lambda: lambda: session)

    rows = await repository.list_recent_liquidation_events("binance_usdm_futures", "BTCUSDT", 25)

    assert len(rows) == 1
    assert rows[0].event_id == event.event_id
    assert rows[0].quantity == Decimal("0.125")
    assert rows[0].price == Decimal("91000.25")
