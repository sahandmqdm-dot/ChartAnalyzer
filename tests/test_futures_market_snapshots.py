import json

import pytest

from market_data_hub import BinanceUSDMPublicAdapter, DataValidationError

NOW = 1_800_000_000.0


def fake_transport(payload):
    body = json.dumps(payload).encode()

    def call(url, timeout):
        assert url.startswith("https://fapi.binance.com/")
        return 200, {}, body

    return call


def test_premium_index_contains_current_observation_and_basis():
    payload = {
        "symbol": "BTCUSDT", "lastFundingRate": "0.0001",
        "markPrice": "101", "indexPrice": "100",
        "nextFundingTime": int((NOW + 8 * 3600) * 1000),
    }
    adapter = BinanceUSDMPublicAdapter(transport=fake_transport(payload), clock=lambda: NOW)
    result = adapter.fetch_premium_index("BTCUSDT")
    assert result["provider"] == "binance_usdm"
    assert result["observed_at"].timestamp() == NOW
    assert result["funding_rate"] == pytest.approx(0.0001)
    assert result["mark_index_basis_pct"] == pytest.approx(1.0)


def test_order_book_snapshot_reports_imbalance_spread_and_snapshot_semantics():
    payload = {"lastUpdateId": 123, "bids": [["100", "5"], ["99", "3"]],
               "asks": [["101", "2"], ["102", "2"]]}
    adapter = BinanceUSDMPublicAdapter(transport=fake_transport(payload), clock=lambda: NOW)
    result = adapter.fetch_order_book("BTCUSDT", 5)
    assert result["last_update_id"] == 123
    assert result["top20_quantity_imbalance"] == pytest.approx(1.0 / 3.0)
    assert result["spread_bps"] == pytest.approx(100.0)
    assert result["sequence_semantics"] == "single_rest_snapshot_not_continuous_diff_stream"


def test_order_book_rejects_crossed_book():
    payload = {"lastUpdateId": 1, "bids": [["101", "1"]], "asks": [["100", "1"]]}
    adapter = BinanceUSDMPublicAdapter(transport=fake_transport(payload), clock=lambda: NOW)
    with pytest.raises(DataValidationError, match="crossed"):
        adapter.fetch_order_book("BTCUSDT", 5)


def test_aggregate_trades_exposes_sampled_taker_flow_not_full_tick_claim():
    payload = [
        {"a": 1, "p": "100", "q": "2", "T": int((NOW - 2) * 1000), "m": False},
        {"a": 2, "p": "101", "q": "1", "T": int((NOW - 1) * 1000), "m": True},
    ]
    adapter = BinanceUSDMPublicAdapter(transport=fake_transport(payload), clock=lambda: NOW)
    result = adapter.fetch_aggregate_trades("BTCUSDT", 2)
    assert result["record_count"] == 2
    assert result["taker_buy_base_quantity"] == pytest.approx(2.0)
    assert result["taker_sell_base_quantity"] == pytest.approx(1.0)
    assert result["sampled_net_taker_flow_base_quantity"] == pytest.approx(1.0)
    assert "not_complete_tick_history" in result["sample_semantics"]


def test_aggregate_trades_rejects_non_boolean_maker_flag():
    payload = [{"p": "100", "q": "1", "T": int(NOW * 1000), "m": "false"}]
    adapter = BinanceUSDMPublicAdapter(transport=fake_transport(payload), clock=lambda: NOW)
    with pytest.raises(DataValidationError, match="maker flag"):
        adapter.fetch_aggregate_trades("BTCUSDT", 1)
