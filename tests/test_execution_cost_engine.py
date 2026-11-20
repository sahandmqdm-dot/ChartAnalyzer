import pytest

from execution_cost_engine import (
    evaluate_execution,
    market_sweep_fills,
    record_execution_cost_experiment,
    validate_fee_schedule,
)


def schedule(**overrides):
    data = {
        "venue": "example", "account_tier": "standard", "market": "spot",
        "maker_rate": 0.001, "taker_rate": 0.002,
        "source": "captured official fee schedule",
        "effective_at": "2026-11-20T00:00:00Z",
    }
    data.update(overrides)
    return data


def run(**overrides):
    args = {
        "side": "buy", "order_type": "market", "requested_quantity": 2,
        "fills": [{"price": 101, "quantity": 2, "liquidity_role": "taker"}],
        "fee_schedule": schedule(), "reference_mid": 100,
        "best_bid": 99.5, "best_ask": 100.5,
        "fill_source": "historical L2 replay",
    }
    args.update(overrides)
    return evaluate_execution(**args)


def test_market_fill_accounts_for_fee_spread_slippage_and_impact():
    result = run(impact_bps=3)
    costs = result["cost_breakdown"]
    assert result["status"] == "filled"
    assert costs["fees"] == pytest.approx(0.404)
    assert costs["spread"] == pytest.approx(1.0)
    assert costs["slippage_beyond_touch"] == pytest.approx(1.0)
    assert costs["market_impact"] == pytest.approx(0.0606)
    assert costs["total"] == pytest.approx(2.4646)


def test_maker_and_taker_fees_are_selected_per_fill_not_order_type():
    result = run(order_type="limit", fills=[
        {"price": 100, "quantity": 1, "liquidity_role": "maker"},
        {"price": 101, "quantity": 1, "liquidity_role": "taker"},
    ])
    assert result["cost_breakdown"]["fees"] == pytest.approx(0.302)
    assert result["status"] == "filled"


def test_limit_order_with_no_fill_is_unfilled_and_has_no_fee():
    result = run(order_type="limit", fills=[], fill_source=None)
    assert result["status"] == "unfilled"
    assert result["filled_quantity"] == 0
    assert result["unfilled_quantity"] == 2
    assert result["cost_breakdown"]["total"] == 0


def test_partial_fill_accounts_only_filled_quantity():
    result = run(fills=[{"price": 100.5, "quantity": 0.5, "liquidity_role": "maker"}])
    assert result["status"] == "partial"
    assert result["filled_quantity"] == pytest.approx(0.5)
    assert result["unfilled_quantity"] == pytest.approx(1.5)
    assert result["cost_breakdown"]["fees"] == pytest.approx(0.05025)


def test_adverse_slippage_is_charged_beyond_touch():
    result = run(fills=[{"price": 102, "quantity": 2, "liquidity_role": "taker"}])
    assert result["cost_breakdown"]["slippage_beyond_touch"] == pytest.approx(3.0)


def test_funding_and_latency_are_explicit_and_traceable():
    result = run(funding_rate=0.0001, funding_events=3, latency_ms=125)
    assert result["cost_breakdown"]["funding"] == pytest.approx(0.0606)
    assert result["execution_context"]["latency_ms"] == 125
    assert result["provenance"]["fee_schedule_source"] == "captured official fee schedule"


@pytest.mark.parametrize("field,value", [
    ("maker_rate", -0.1), ("taker_rate", float("nan")),
    ("effective_at", ""), ("source", ""),
])
def test_invalid_fee_schedule_fails_closed(field, value):
    with pytest.raises(ValueError):
        validate_fee_schedule(schedule(**{field: value}))


def test_missing_fee_schedule_or_fill_provenance_fails_closed():
    with pytest.raises(ValueError, match="fee_schedule"):
        run(fee_schedule=None)
    with pytest.raises(ValueError, match="fill_source"):
        run(fill_source=None)


def test_fill_quantity_cannot_exceed_requested_and_liquidity_role_is_required():
    with pytest.raises(ValueError, match="cannot exceed"):
        run(fills=[{"price": 100, "quantity": 3, "liquidity_role": "taker"}])
    with pytest.raises(ValueError, match="liquidity_role"):
        run(fills=[{"price": 100, "quantity": 1}])


def test_sell_side_costs_use_bid_touch_and_preserve_side_direction():
    result = run(side="sell", fills=[
        {"price": 98, "quantity": 1, "liquidity_role": "taker"},
    ])
    assert result["cost_breakdown"]["spread"] == pytest.approx(0.5)
    assert result["cost_breakdown"]["slippage_beyond_touch"] == pytest.approx(1.5)


def test_rejected_order_is_distinct_from_unfilled_order():
    result = run(order_type="limit", fills=[], fill_source=None,
                 rejection_reason="venue rejected minimum notional")
    assert result["status"] == "rejected"
    assert result["fill_count"] == 0


def test_missing_impact_and_funding_inputs_are_flagged_incomplete():
    result = run(funding_events=2)
    assert result["cost_breakdown"]["complete"] is False
    assert result["cost_breakdown"]["missing_inputs"] == ["impact_bps", "funding_rate"]


def test_fill_probability_requires_calibration_and_source():
    with pytest.raises(ValueError, match="calibrated model provenance"):
        run(order_type="limit", fill_probability=0.7)
    result = run(order_type="limit", fill_probability=0.7, fill_probability_source="validated queue model",
                 fill_probability_calibrated=True)
    assert result["execution_context"]["fill_probability"] == pytest.approx(0.7)


def test_rejected_order_cannot_claim_fills():
    with pytest.raises(ValueError, match="cannot contain fills"):
        run(rejection_reason="venue rejection")


def test_l2_market_sweep_consumes_asks_and_returns_partial_when_depth_is_short():
    book = {
        "market": "spot", "symbol": "BTCUSDT", "valid": True,
        "last_update_id": 1234,
        "asks": [(101.0, 0.4), (101.5, 0.6)],
        "bids": [(100.0, 0.7), (99.5, 0.8)],
    }
    sweep = market_sweep_fills(
        book, side="buy", requested_quantity=1.5,
        book_received_at="2026-11-20T12:00:00.000Z",
        decision_at="2026-11-20T12:00:00.100Z",
    )
    assert sweep["estimated_filled_quantity"] == pytest.approx(1.0)
    assert sweep["estimated_unfilled_quantity"] == pytest.approx(0.5)
    assert sweep["is_full_depth_fill"] is False
    assert [fill["price"] for fill in sweep["fills"]] == [101.0, 101.5]
    assert all(fill["liquidity_role"] == "taker" for fill in sweep["fills"])


def test_l2_market_sweep_uses_bid_side_for_sell():
    book = {
        "market": "usdm", "symbol": "BTCUSDT", "valid": True,
        "last_update_id": 42,
        "asks": [(101.0, 2.0)], "bids": [(100.0, 0.5), (99.0, 2.0)],
    }
    sweep = market_sweep_fills(
        book, side="sell", requested_quantity=1.0,
        book_received_at="2026-11-20T12:00:00Z",
        decision_at="2026-11-20T12:00:00.010Z",
    )
    assert [fill["price"] for fill in sweep["fills"]] == [100.0, 99.0]
    assert sweep["estimated_filled_quantity"] == pytest.approx(1.0)


@pytest.mark.parametrize("received,decision", [
    ("2026-11-20T12:00:01Z", "2026-11-20T12:00:00Z"),
    ("2026-11-20T11:59:00Z", "2026-11-20T12:00:00Z"),
])
def test_l2_market_sweep_rejects_future_or_stale_book(received, decision):
    book = {
        "market": "spot", "symbol": "BTCUSDT", "valid": True,
        "last_update_id": 1, "asks": [(101.0, 1.0)], "bids": [(100.0, 1.0)],
    }
    with pytest.raises(ValueError):
        market_sweep_fills(
            book, side="buy", requested_quantity=1,
            book_received_at=received, decision_at=decision, max_age_ms=500,
        )


def test_l2_market_sweep_rejects_invalid_sequence_book_and_unsorted_levels():
    book = {
        "market": "spot", "symbol": "BTCUSDT", "valid": False,
        "last_update_id": 1, "asks": [(101.0, 1.0)], "bids": [(100.0, 1.0)],
    }
    kwargs = {
        "side": "buy", "requested_quantity": 1,
        "book_received_at": "2026-11-20T12:00:00Z",
        "decision_at": "2026-11-20T12:00:00Z",
    }
    with pytest.raises(ValueError, match="sequence-valid"):
        market_sweep_fills(book, **kwargs)
    book["valid"] = True
    book["asks"] = [(102.0, 1.0), (101.0, 1.0)]
    with pytest.raises(ValueError, match="sorted"):
        market_sweep_fills(book, **kwargs)


def test_market_order_cannot_claim_maker_liquidity():
    with pytest.raises(ValueError, match="must be taker"):
        run(fills=[{"price": 100, "quantity": 1, "liquidity_role": "maker"}])


def test_execution_cost_result_is_registered_with_provenance_and_completeness(tmp_path):
    from research_registry import ResearchRegistry

    registry = ResearchRegistry(tmp_path / "research.db")
    registry.register("cost-hypothesis-v1", "Execution cost accounting",
                      {"purpose": "frozen execution cost model"})
    complete_result = run(impact_bps=0)
    recorded = record_execution_cost_experiment(
        registry, experiment_id="cost-exp-001", hypothesis_id="cost-hypothesis-v1",
        dataset_id="dataset-abc", config_hash="config-abc", source="binance_spot",
        symbol="BTCUSDT", timeframe="1m", execution_result=complete_result,
        run_id="replay-001",
    )
    assert recorded["created"] is True
    assert recorded["registry_status"] == "COMPLETED"

    summary = registry.summary()
    metrics = next(item["metrics"] for item in summary["recent_experiments"]
                   if item["experiment_id"] == "cost-exp-001")
    assert metrics["fee_schedule_provenance"]["fee_schedule_hash"]
    assert metrics["cost_model_complete"] is True

    incomplete_result = run()
    incomplete = record_execution_cost_experiment(
        registry, experiment_id="cost-exp-002", hypothesis_id="cost-hypothesis-v1",
        dataset_id="dataset-abc", config_hash="config-abc", source="binance_spot",
        symbol="BTCUSDT", timeframe="1m", execution_result=incomplete_result,
    )
    assert incomplete["registry_status"] == "INCOMPLETE_COST_MODEL"
    assert registry.summary()["failed_experiments"] == 1
