import pytest

from execution_cost_engine import evaluate_execution, validate_fee_schedule


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
