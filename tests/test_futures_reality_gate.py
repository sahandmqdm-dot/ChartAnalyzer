from datetime import UTC, datetime, timedelta

from futures_reality_gate import evaluate_futures_reality_gate


NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


def inputs():
    return {
        "instrument_id": "BTCUSDT",
        "venue": "binance",
        "as_of": NOW,
        "contract": {
            "exchange": "binance", "symbol": "BTCUSDT", "market_type": "futures",
            "contract_type": "linear", "quote_asset": "USDT", "margin_asset": "USDT",
            "settlement_asset": "USDT", "contract_multiplier": "1", "tick_size": "0.1",
            "step_size": "0.001", "min_qty": "0.001", "min_notional": "5",
            "max_qty": "1000", "max_leverage": "125",
            "maintenance_margin_tiers": [{"notional_cap": "50000", "maintenance_margin_rate": "0.004"}],
            "source": "exchange-info", "observed_at": NOW - timedelta(minutes=5),
            "effective_at": NOW - timedelta(days=1),
        },
        "prices": {
            "exchange": "binance", "symbol": "BTCUSDT", "market_type": "futures",
            "last": "90000", "mark": "90001", "index": "89998", "bid": "89999",
            "ask": "90000", "source": "premium-index+book", "observed_at": NOW - timedelta(seconds=1),
        },
        "funding": {
            "exchange": "binance", "symbol": "BTCUSDT", "market_type": "futures",
            "funding_rate": "0.0001", "observed_at": NOW - timedelta(seconds=10),
            "next_funding_time": NOW + timedelta(hours=7, minutes=59), "source": "premium-index",
        },
        "execution": {
            "fee_schedule_source": "configured-account-fee", "fee_schedule_version": "test-v1",
            "maker_fee_rate": "0.0002", "taker_fee_rate": "0.0005",
            "impact_model_source": "calibration-study", "impact_model_version": "impact-v1",
        },
        "margin_model_status": "exchange_tier_aware",
    }


def evaluate(**overrides):
    values = inputs()
    values.update(overrides)
    return evaluate_futures_reality_gate(**values)


def test_complete_fresh_inputs_are_research_ready_but_never_actionable():
    result = evaluate()
    assert result["status"] == "READY_FOR_RESEARCH"
    assert result["research_ready"] is True
    assert result["actionable"] is False
    assert result["deployment_authorized"] is False
    assert result["contract_fingerprint"]


def test_stale_contract_metadata_blocks():
    values = inputs()
    values["contract"]["observed_at"] = NOW - timedelta(days=3)
    result = evaluate(**values)
    assert result["status"] == "NOT_READY"
    assert any("contract.observed_at is stale" in item for item in result["blockers"])


def test_cross_instrument_data_cannot_be_mixed():
    values = inputs()
    values["prices"]["symbol"] = "ETHUSDT"
    result = evaluate(**values)
    assert any("price symbol does not match" in item for item in result["blockers"])


def test_invalid_or_crossed_book_blocks():
    values = inputs()
    values["prices"]["bid"] = "90001"
    values["prices"]["ask"] = "90000"
    result = evaluate(**values)
    assert any("best bid exceeds best ask" in item for item in result["blockers"])


def test_stale_funding_and_unknown_margin_model_block():
    values = inputs()
    values["funding"]["observed_at"] = NOW - timedelta(hours=2)
    values["margin_model_status"] = "generic_estimate"
    result = evaluate(**values)
    assert any("funding.observed_at is stale" in item for item in result["blockers"])
    assert any("not verified as exchange-tier-aware" in item for item in result["blockers"])


def test_missing_impact_provenance_blocks():
    values = inputs()
    values["execution"].pop("impact_model_source")
    result = evaluate(**values)
    assert any("execution.impact_model_source is missing" in item for item in result["blockers"])


def test_future_dated_market_observation_blocks():
    values = inputs()
    values["prices"]["observed_at"] = NOW + timedelta(seconds=1)
    result = evaluate(**values)
    assert any("prices.observed_at is in the future" in item for item in result["blockers"])


def test_contract_identity_mismatch_blocks():
    values = inputs()
    values["contract"]["contract_type"] = "inverse"
    result = evaluate(**values)
    assert any("only linear contracts" in item for item in result["blockers"])
