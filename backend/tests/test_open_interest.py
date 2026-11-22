from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.market_data.open_interest import (
    BinanceOpenInterestCollector,
    normalize_open_interest,
)


def test_normalize_open_interest_accepts_valid_binance_payload():
    result = normalize_open_interest({
        "symbol": "BTCUSDT",
        "openInterest": "12345.678",
        "time": 1_800_000_000_000,
    }, "BTCUSDT")
    assert result is not None
    assert result["open_interest"] == Decimal("12345.678")
    assert result["observed_at"] == datetime.fromtimestamp(1_800_000_000, tz=UTC)


@pytest.mark.parametrize("payload", [
    {"symbol": "ETHUSDT", "openInterest": "100", "time": 1_800_000_000_000},
    {"symbol": "BTCUSDT", "openInterest": "0", "time": 1_800_000_000_000},
    {"symbol": "BTCUSDT", "openInterest": "NaN", "time": 1_800_000_000_000},
    {"symbol": "BTCUSDT", "openInterest": "-1", "time": 1_800_000_000_000},
    {"symbol": "BTCUSDT", "openInterest": "1", "time": "invalid"},
])
def test_normalize_open_interest_rejects_invalid_payloads(payload):
    assert normalize_open_interest(payload, "BTCUSDT") is None


def test_collector_requires_reasonable_poll_interval():
    with pytest.raises(ValueError, match="at least 10"):
        BinanceOpenInterestCollector("BTCUSDT", interval_seconds=1)


@pytest.mark.asyncio
async def test_collector_poll_updates_snapshot_and_persists(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "symbol": "BTCUSDT",
                "openInterest": "200.5",
                "time": 1_800_000_000_000,
            }

    class FakeClient:
        async def get(self, endpoint, params):
            assert endpoint.endswith("/fapi/v1/openInterest")
            assert params == {"symbol": "BTCUSDT"}
            return FakeResponse()

    calls = []

    async def fake_save(provider, symbol, observed_at, open_interest):
        calls.append((provider, symbol, observed_at, open_interest))
        return True

    monkeypatch.setattr(
        "app.market_data.open_interest_repository.save_open_interest", fake_save
    )
    monkeypatch.setattr("app.database.database_enabled", lambda: True)
    collector = BinanceOpenInterestCollector("BTCUSDT")
    await collector._poll_once(FakeClient())

    snapshot = collector.snapshot()
    assert snapshot["open_interest"] == "200.5"
    assert snapshot["persistence_state"] == "available"
    assert len(calls) == 1
    assert calls[0][0:2] == ("binance_usdm_futures", "BTCUSDT")
    assert calls[0][3] == Decimal("200.5")
