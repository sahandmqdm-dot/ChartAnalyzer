from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.market_data import liquidation_repository
from app.market_data.liquidation_stream import LiquidationCollector, LiquidationEvent


def sample_event():
    return LiquidationEvent(
        event_id="b" * 24,
        provider="bybit_linear_futures",
        symbol="BTCUSDT",
        event_time=datetime(2026, 11, 21, tzinfo=UTC),
        liquidation_side="short",
        order_side="SELL",
        quantity=Decimal("2"),
        price=Decimal("90000"),
        notional=Decimal("180000"),
        price_kind="bankruptcy",
    )


@pytest.mark.asyncio
async def test_collector_persists_event_and_reports_storage_state(monkeypatch):
    calls = []

    async def save(event):
        calls.append(event.event_id)
        return True

    monkeypatch.setattr(liquidation_repository, "save_liquidation_event", save)
    collector = LiquidationCollector("bybit_linear_futures", "BTCUSDT")

    await collector.ingest(sample_event())

    snapshot = collector.snapshot()
    assert calls == ["b" * 24]
    assert snapshot["status"]["persistence_state"] == "available"
    assert snapshot["retention"] == "postgresql_and_bounded_memory"


@pytest.mark.asyncio
async def test_database_failure_does_not_drop_event_or_stop_collector(monkeypatch):
    async def fail(_event):
        raise ConnectionError("database credentials or host details must not leak")

    monkeypatch.setattr(liquidation_repository, "save_liquidation_event", fail)
    collector = LiquidationCollector("bybit_linear_futures", "BTCUSDT")

    await collector.ingest(sample_event())

    snapshot = collector.snapshot()
    assert len(collector.events) == 1
    assert snapshot["status"]["persistence_state"] == "unavailable"
    assert snapshot["status"]["persistence_error"] == "ConnectionError"
    assert "credentials" not in str(snapshot)
