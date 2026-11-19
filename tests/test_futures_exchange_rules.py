import pytest

from futures_exchange_rules import apply_futures_filters, validate_futures_rules


@pytest.fixture
def rules():
    return {
        "exchange": "generic",
        "symbol": "BTCUSDT",
        "status": "TRADING",
        "contract_type": "linear",
        "quote_asset": "USDT",
        "tick_size": "0.1",
        "step_size": "0.001",
        "min_qty": "0.001",
        "max_qty": "100",
        "min_notional": "5",
        "max_leverage": "50",
        "maintenance_margin_tiers": [
            {"notional_cap": "50000", "maintenance_margin_rate": "0.004", "max_leverage": "50"},
            {"notional_cap": "250000", "maintenance_margin_rate": "0.01", "max_leverage": "25"},
        ],
    }


@pytest.fixture
def plan():
    return {
        "status": "READY_FOR_REVIEW",
        "actionable": False,
        "market_type": "futures",
        "side": "long",
        "entry_reference": 68000,
        "quantity_base": 0.012345,
        "leverage": 5,
        "stop_loss": 67000.04,
        "targets": [
            {"label": "TP1", "price": 70000.09},
            {"label": "TP2", "price": 71000.01},
        ],
    }


def test_validates_complete_linear_contract_metadata(rules):
    assert validate_futures_rules(rules) is rules


@pytest.mark.parametrize("change, message", [
    ({"status": "HALT"}, "not in TRADING"),
    ({"contract_type": "inverse"}, "only linear"),
    ({"tick_size": None}, "missing exchange futures rules"),
    ({"maintenance_margin_tiers": []}, "non-empty list"),
    ({"max_leverage": 0}, "max_leverage must be positive"),
])
def test_rejects_incomplete_or_unsupported_contract_rules(rules, change, message):
    rules.update(change)
    with pytest.raises(ValueError, match=message):
        validate_futures_rules(rules)


def test_rejects_unsorted_maintenance_tiers(rules):
    rules["maintenance_margin_tiers"].reverse()
    with pytest.raises(ValueError, match="strictly increasing"):
        validate_futures_rules(rules)


def test_quantity_rounds_down_and_prices_round_conservatively(rules, plan):
    out = apply_futures_filters(plan, rules)
    assert out["status"] == "READY_FOR_REVIEW"
    assert out["quantity_base"] == pytest.approx(0.012)
    assert out["stop_loss"] == pytest.approx(67000.1)
    assert out["targets"][0]["price"] == pytest.approx(70000.0)
    assert out["actionable"] is False


def test_minimum_notional_blocks_after_quantity_rounding(rules, plan):
    rules["min_notional"] = "1000"
    out = apply_futures_filters(plan, rules)
    assert out["status"] == "NO_TRADE"
    assert any("minimum" in x for x in out["blockers"])


def test_tier_leverage_limit_is_enforced(rules, plan):
    plan["leverage"] = 30
    plan["quantity_base"] = 1
    out = apply_futures_filters(plan, rules)
    assert out["status"] == "NO_TRADE"
    assert any("tier maximum" in x for x in out["blockers"])


def test_notional_above_highest_tier_is_blocked(rules, plan):
    plan["quantity_base"] = 10
    out = apply_futures_filters(plan, rules)
    assert out["status"] == "NO_TRADE"
    assert any("highest published" in x for x in out["blockers"])


def test_short_rounding_does_not_expand_stop_risk(rules, plan):
    plan.update({"side": "short", "entry_reference": 68000, "stop_loss": 68999.96,
                 "targets": [{"label": "TP1", "price": 66000.01}]})
    out = apply_futures_filters(plan, rules)
    assert out["stop_loss"] == pytest.approx(68999.9)
    assert out["targets"][0]["price"] == pytest.approx(66000.1)


def test_non_review_plan_is_fail_closed(rules, plan):
    plan["status"] = "NO_TRADE"
    out = apply_futures_filters(plan, rules)
    assert out["status"] == "NO_TRADE"
    assert out["actionable"] is False
