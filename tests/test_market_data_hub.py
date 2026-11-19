from datetime import UTC, datetime
import json

import pytest

from market_data_hub import (
    BinanceUSDMPublicAdapter,
    DataValidationError,
    MarketDataError,
    MarketDataHub,
    OHLCVBar,
    OHLCVBatch,
    ProviderConfig,
    ProviderRateLimitError,
    ProviderUnavailableError,
)


NOW = 1_800_000_000.0


def fake_transport(payload, status=200, headers=None):
    body = json.dumps(payload).encode()

    def call(url, timeout):
        assert "https://fapi.binance.com/" in url
        assert timeout > 0
        return status, headers or {}, body

    return call


def kline(open_ms=NOW * 1000 - 3_600_000, close_ms=NOW * 1000 - 1):
    return [int(open_ms), "100", "110", "90", "105", "12.5",
            int(close_ms), "1300", 42, "6", "630", "0"]


def test_ohlcv_normalization_and_provenance():
    adapter = BinanceUSDMPublicAdapter(transport=fake_transport([kline()]), clock=lambda: NOW)
    batch = adapter.fetch_ohlcv("BTCUSDT", "1h", 1)
    assert isinstance(batch, OHLCVBatch)
    assert batch.bars[0].instrument_id == "BTCUSDT"
    assert batch.bars[0].provider == "binance_usdm"
    assert batch.bars[0].close == 105
    assert batch.provenance.source_timestamp_semantics == "exchange_kline_open_time_utc"
    assert batch.provenance.raw_record_count == 1


def test_duplicate_timestamps_are_deduplicated_with_warning():
    row = kline()
    adapter = BinanceUSDMPublicAdapter(transport=fake_transport([row, row]), clock=lambda: NOW)
    batch = adapter.fetch_ohlcv("BTCUSDT", "1h", 2)
    assert len(batch.bars) == 1
    assert any("duplicate" in warning for warning in batch.warnings)


def test_candle_gap_is_reported_not_filled():
    first = kline(NOW * 1000 - 10_800_000, NOW * 1000 - 7_200_001)
    second = kline(NOW * 1000 - 3_600_000, NOW * 1000 - 1)
    adapter = BinanceUSDMPublicAdapter(transport=fake_transport([first, second]), clock=lambda: NOW)
    batch = adapter.fetch_ohlcv("BTCUSDT", "1h", 2)
    assert len(batch.bars) == 2
    assert any("gaps" in warning for warning in batch.warnings)


@pytest.mark.parametrize("row", [
    [1, "100", "90", "95", "105", "1", 2, "100"],
    [1, "nan", "110", "90", "105", "1", 2, "100"],
])
def test_invalid_candle_payload_fails_closed(row):
    adapter = BinanceUSDMPublicAdapter(transport=fake_transport([row]), clock=lambda: NOW)
    with pytest.raises(DataValidationError):
        adapter.fetch_ohlcv("BTCUSDT", "1h", 1)


def test_bad_symbols_and_intervals_are_rejected_before_transport():
    adapter = BinanceUSDMPublicAdapter(transport=fake_transport([]), clock=lambda: NOW)
    with pytest.raises(ValueError):
        adapter.fetch_ohlcv("BTC/USDT", "1h", 10)
    with pytest.raises(ValueError):
        adapter.fetch_ohlcv("BTCUSDT", "2w", 10)


def test_rate_limit_retries_then_succeeds():
    calls, sleeps = [], []

    def transport(url, timeout):
        calls.append(url)
        if len(calls) == 1:
            return 429, {"Retry-After": "0"}, b'{"code":-1003}'
        return 200, {}, json.dumps([kline()]).encode()

    adapter = BinanceUSDMPublicAdapter(
        ProviderConfig(max_retries=1, backoff_seconds=0),
        transport=transport, clock=lambda: NOW, sleeper=sleeps.append,
    )
    assert len(adapter.fetch_ohlcv("BTCUSDT", "1h", 1).bars) == 1
    assert len(calls) == 2


def test_rate_limit_exhaustion_is_explicit():
    adapter = BinanceUSDMPublicAdapter(
        ProviderConfig(max_retries=1, backoff_seconds=0),
        transport=fake_transport([], status=429), clock=lambda: NOW, sleeper=lambda _: None,
    )
    with pytest.raises(ProviderRateLimitError):
        adapter.fetch_ohlcv("BTCUSDT", "1h", 1)


def test_server_failure_fails_closed():
    adapter = BinanceUSDMPublicAdapter(
        ProviderConfig(max_retries=0),
        transport=fake_transport({}, status=503), clock=lambda: NOW,
    )
    with pytest.raises(ProviderUnavailableError):
        adapter.fetch_ohlcv("BTCUSDT", "1h", 1)


def test_funding_history_normalizes_exchange_event_time():
    rows = [{"symbol": "BTCUSDT", "fundingTime": int(NOW * 1000),
             "fundingRate": "0.0001", "markPrice": "100"}]
    adapter = BinanceUSDMPublicAdapter(transport=fake_transport(rows), clock=lambda: NOW)
    result = adapter.fetch_funding_history("BTCUSDT")
    assert result["records"][0]["funding_rate"] == pytest.approx(0.0001)
    assert result["records"][0]["source_timestamp_semantics"] == "exchange_funding_event_time"


def test_open_interest_history_keeps_unit_and_quote_notional():
    rows = [{"symbol": "BTCUSDT", "sumOpenInterest": "1200",
             "sumOpenInterestValue": "120000000", "timestamp": int(NOW * 1000)}]
    adapter = BinanceUSDMPublicAdapter(transport=fake_transport(rows), clock=lambda: NOW)
    result = adapter.fetch_open_interest_history("BTCUSDT")
    assert result["records"][0]["unit"] == "contracts"
    assert result["records"][0]["quote_notional"] == 120000000


def test_hub_rejects_unknown_provider_and_mismatched_batch_provider():
    hub = MarketDataHub()
    with pytest.raises(MarketDataError):
        hub.fetch_ohlcv("missing", "BTCUSDT")
    class BadAdapter:
        def fetch_ohlcv(self, *args):
            return OHLCVBatch((), type("P", (), {"provider": "wrong"})())
        fetch_funding_history = fetch_open_interest_history = lambda *args: {}
    hub.register("expected", BadAdapter())
    with pytest.raises(DataValidationError):
        hub.fetch_ohlcv("expected", "BTCUSDT")


def test_hub_derivatives_output_is_never_actionable():
    class Adapter:
        def fetch_ohlcv(self, *args):
            return OHLCVBatch((), None)
        def fetch_funding_history(self, *args):
            return {"provider": "mock", "market_type": "linear_perpetual", "records": []}
        def fetch_open_interest_history(self, *args):
            return {"provider": "mock", "market_type": "linear_perpetual", "records": []}
    hub = MarketDataHub({"mock": Adapter()})
    result = hub.fetch_derivatives("mock", "BTCUSDT")
    assert result["actionable"] is False


def test_ohlcv_bar_rejects_naive_time_and_negative_volume():
    with pytest.raises(DataValidationError):
        OHLCVBar("BTCUSDT", "x", "1h", datetime(2026, 1, 1),
                 datetime(2026, 1, 1, 1, tzinfo=UTC), 1, 2, 0.5, 1.5, 1)
    with pytest.raises(DataValidationError):
        OHLCVBar("BTCUSDT", "x", "1h", datetime(2026, 1, 1, tzinfo=UTC),
                 datetime(2026, 1, 1, 1, tzinfo=UTC), 1, 2, 0.5, 1.5, -1)
