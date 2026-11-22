from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.market_data import open_interest_repository as repository


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
async def test_save_open_interest_is_idempotent(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(repository, "get_session_factory", lambda: lambda: session)
    saved = await repository.save_open_interest(
        "binance_usdm_futures", "BTCUSDT",
        datetime(2026, 11, 22, 12, 0, tzinfo=UTC), Decimal("100.25"),
    )
    assert saved is True
    assert session.committed
    sql = str(session.statement.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (observation_id) DO NOTHING" in sql


@pytest.mark.asyncio
async def test_list_open_interest_serializes_decimal_as_string(monkeypatch):
    row = SimpleNamespace(
        provider="binance_usdm_futures",
        symbol="BTCUSDT",
        observed_at=datetime(2026, 11, 22, 12, 0, tzinfo=UTC),
        open_interest=Decimal("100.25"),
    )
    session = FakeSession(rows=[row])
    monkeypatch.setattr(repository, "get_session_factory", lambda: lambda: session)
    rows = await repository.list_open_interest(
        "BTCUSDT", datetime(2026, 11, 22, tzinfo=UTC), limit=10,
    )
    assert rows == [{
        "provider": "binance_usdm_futures",
        "symbol": "BTCUSDT",
        "observed_at": "2026-11-22T12:00:00+00:00",
        "open_interest": "100.25",
    }]


@pytest.mark.asyncio
async def test_open_interest_repository_reports_disabled_database(monkeypatch):
    monkeypatch.setattr(repository, "get_session_factory", lambda: None)
    assert await repository.save_open_interest(
        "binance_usdm_futures", "BTCUSDT", datetime.now(UTC), Decimal(10)
    ) is None
    assert await repository.list_open_interest(
        "BTCUSDT", datetime(2026, 1, 1, tzinfo=UTC)
    ) is None


@pytest.mark.asyncio
async def test_open_interest_history_rejects_invalid_limit():
    with pytest.raises(ValueError, match="limit"):
        await repository.list_open_interest(
            "BTCUSDT", datetime(2026, 1, 1, tzinfo=UTC), limit=5001
        )
