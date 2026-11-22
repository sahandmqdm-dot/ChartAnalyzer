from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.market_data import futures_snapshot_repository as repository


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


def snapshot_record():
    return SimpleNamespace(
        provider="binance_usdm_futures",
        symbol="BTCUSDT",
        observed_at=datetime(2026, 11, 22, 12, 0, tzinfo=UTC),
        mark_price=Decimal("91000.25"),
        index_price=Decimal("90998.5"),
        funding_rate=Decimal("0.0001"),
        next_funding_time=datetime(2026, 11, 22, 16, 0, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_save_mark_snapshot_is_conflict_safe_and_commits(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(repository, "get_session_factory", lambda: lambda: session)

    saved = await repository.save_futures_mark_snapshot(
        "binance_usdm_futures", "BTCUSDT", snapshot_record().observed_at,
        Decimal("91000.25"), Decimal("90998.5"), Decimal("0.0001"),
        snapshot_record().next_funding_time,
    )

    assert saved is True
    assert session.committed
    sql = str(session.statement.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (snapshot_id) DO NOTHING" in sql


@pytest.mark.asyncio
async def test_mark_snapshot_history_returns_decimal_strings(monkeypatch):
    session = FakeSession(rows=[snapshot_record()])
    monkeypatch.setattr(repository, "get_session_factory", lambda: lambda: session)

    rows = await repository.list_futures_mark_snapshots(
        "BTCUSDT", datetime(2026, 11, 22, tzinfo=UTC), limit=20,
    )

    assert len(rows) == 1
    assert rows[0]["mark_price"] == "91000.25"
    assert rows[0]["funding_rate"] == "0.0001"
    assert rows[0]["symbol"] == "BTCUSDT"


@pytest.mark.asyncio
async def test_mark_snapshot_repository_handles_disabled_database(monkeypatch):
    monkeypatch.setattr(repository, "get_session_factory", lambda: None)

    result = await repository.save_futures_mark_snapshot(
        "binance_usdm_futures", "BTCUSDT", datetime.now(UTC),
        Decimal("100"), Decimal("99"), Decimal("0.001"), datetime.now(UTC),
    )
    history = await repository.list_futures_mark_snapshots(
        "BTCUSDT", datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert result is None
    assert history is None


@pytest.mark.asyncio
async def test_mark_snapshot_history_rejects_excessive_limit():
    with pytest.raises(ValueError, match="limit"):
        await repository.list_futures_mark_snapshots(
            "BTCUSDT", datetime(2026, 1, 1, tzinfo=UTC), limit=5001,
        )
