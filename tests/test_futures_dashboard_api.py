from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import app as chart_app
import futures_signal_engine
import market_data_hub


NOW = datetime.now(UTC)


class FakeFuturesAdapter:
    def __init__(self, config=None):
        self.config = config

    def fetch_ohlcv(self, symbol, interval, limit):
        bars = []
        for index in range(80):
            close_time = NOW - timedelta(minutes=1) - timedelta(seconds=(79-index) * 3600)
            bars.append(SimpleNamespace(
                close_time=close_time, open_time=close_time-timedelta(hours=1),
                open=100.0, high=102.0, low=99.0, close=101.0, volume=10.0,
            ))
        provenance = SimpleNamespace(
            provider="binance_usdm", market_type="linear_perpetual",
            endpoint="/fapi/v1/klines", retrieved_at=NOW,
            raw_record_count=80,
        )
        return SimpleNamespace(bars=tuple(bars), provenance=provenance, warnings=())

    def fetch_premium_index(self, symbol):
        return {
            "funding_rate": 0.0001, "observed_at": NOW,
            "mark_price": 101.0, "index_price": 100.0,
            "next_funding_at": NOW+timedelta(hours=8),
            "mark_index_basis_pct": 1.0,
            "funding_rate_semantics": "latest_exchange_indicative_funding_rate",
        }

    def fetch_open_interest_history(self, symbol, period, limit):
        return {"records": [
            {"observed_at": NOW-timedelta(minutes=60), "open_interest": 100.0, "unit": "contracts"},
            {"observed_at": NOW-timedelta(minutes=5), "open_interest": 110.0, "unit": "contracts"},
        ]}

    def fetch_order_book(self, symbol, limit):
        return {
            "retrieved_at": NOW, "best_bid": 100.0, "best_ask": 101.0,
            "spread_bps": 99.5, "top20_quantity_imbalance": 0.2,
            "last_update_id": 123, "sequence_semantics": "single_rest_snapshot_not_continuous_diff_stream",
        }

    def fetch_aggregate_trades(self, symbol, limit):
        return {
            "retrieved_at": NOW, "record_count": 25,
            "taker_buy_base_quantity": 15.0, "taker_sell_base_quantity": 10.0,
            "sampled_net_taker_flow_base_quantity": 5.0,
            "sample_semantics": "bounded_recent_aggregate_trade_sample_not_complete_tick_history",
        }


def test_futures_scenarios_endpoint_is_registered_and_returns_read_only_context(monkeypatch):
    monkeypatch.setattr(market_data_hub, "BinanceUSDMPublicAdapter", FakeFuturesAdapter)
    monkeypatch.setattr(
        futures_signal_engine, "analyze_futures_scenarios",
        lambda *args, **kwargs: {
            "candidate": "LONG", "scenarios": {}, "derivatives_context": {},
            "blockers": ["no futures OOS authorization"],
        },
    )
    assert ("GET", "/api/futures/scenarios") in chart_app.app.routes
    result = chart_app.api_futures_scenarios("BTCUSDT", "1h", 80)
    assert result["candidate"] == "LONG"
    assert result["decision"] if "decision" in result else True
    assert result["microstructure_context"]["order_book"]["last_update_id"] == 123
    assert result["microstructure_context"]["aggregate_trades"]["sample_count"] == 25
    assert result["microstructure_context"]["used_in_directional_score"] is False
    assert result["derivatives_context"]["open_interest_baseline_change_pct"] == pytest.approx(10.0)


@pytest.mark.parametrize("symbol,tf,limit", [
    ("../BTC", "1h", 240),
    ("BTCUSDT", "3m", 240),
    ("BTCUSDT", "1h", 20),
])
def test_futures_scenarios_endpoint_rejects_invalid_request(symbol, tf, limit):
    with pytest.raises(chart_app.HTTPException):
        chart_app.api_futures_scenarios(symbol, tf, limit)
