import pytest
from equity_market_rules import apply_equity_filters, validate_equity_rules


@pytest.fixture
def rules():
    return {
        "symbol": "AAPL", "venue": "NASDAQ", "status": "TRADING",
        "tick_size": "0.01", "lot_size": "1", "min_qty": "1",
        "max_qty": "100000", "min_notional": "1", "shortable": True,
        "borrow_available": True, "ssr_active": False,
    }


@pytest.fixture
def plan():
    return {
        "status": "READY_FOR_REVIEW", "actionable": False,
        "market_type": "equity", "side": "long",
        "entry_reference": 200.00, "quantity": 10.7, "stop_loss": 195.004,
        "targets": [{"label": "TP1", "price": 210.009}],
    }


def test_valid_long_rounds_quantity_and_prices(rules, plan):
    out = apply_equity_filters(plan, rules)
    assert out["status"] == "READY_FOR_REVIEW"
    assert out["quantity"] == pytest.approx(10)
    assert out["stop_loss"] == pytest.approx(195.01)
    assert out["targets"][0]["price"] == pytest.approx(210.00)
    assert out["actionable"] is False


def test_short_requires_shortable_and_borrow(rules, plan):
    plan.update({"side": "short", "stop_loss": 205, "targets": [{"price": 190}]})
    rules["shortable"] = False
    rules["borrow_available"] = False
    out = apply_equity_filters(plan, rules)
    assert out["status"] == "NO_TRADE"
    assert any("not marked shortable" in x for x in out["blockers"])
    assert any("borrow availability" in x for x in out["blockers"])


def test_ssr_requires_price_test_confirmation(rules, plan):
    plan.update({"side": "short", "stop_loss": 205, "targets": [{"price": 190}]})
    rules["ssr_active"] = True
    out = apply_equity_filters(plan, rules)
    assert out["status"] == "NO_TRADE"
    assert any("price-test compliance" in x for x in out["blockers"])


def test_minimum_notional_and_quantity_enforced(rules, plan):
    plan["quantity"] = 0.5
    rules["min_notional"] = "1000"
    out = apply_equity_filters(plan, rules)
    assert out["status"] == "NO_TRADE"
    assert len(out["blockers"]) >= 2


@pytest.mark.parametrize("change", [
    {"status": "HALTED"}, {"tick_size": 0}, {"lot_size": 0},
    {"min_qty": 10, "max_qty": 1}, {"venue": "OTC"},
])
def test_invalid_rules_fail_closed(rules, change):
    rules.update(change)
    with pytest.raises(ValueError):
        validate_equity_rules(rules)


def test_non_review_plan_is_not_actionable(rules, plan):
    plan["status"] = "NO_TRADE"
    out = apply_equity_filters(plan, rules)
    assert out["status"] == "NO_TRADE"
    assert out["actionable"] is False
