import pytest
from adaptive_trade_planner import build_trade_plan

def plan(**kw):
    args=dict(side="long",entry_price=68000,atr=500,account_equity=1000,
        market_type="futures",risk_fraction=.005,max_leverage=5,
        signal_valid=True,data_integrity_valid=True,oos_authorized=True,
        deployment_gate={"eligible":True})
    args.update(kw)
    return build_trade_plan(**args)

def test_risk_budget_and_quantity():
    p=plan()
    assert p["risk_budget"]==pytest.approx(5)
    assert p["modeled_total_loss_at_stop"]<=5+1e-9
    assert p["status"]=="READY_FOR_REVIEW" and p["actionable"] is False

def test_directional_stops_targets():
    long=plan()
    short=plan(side="short")
    assert long["stop_loss"]<long["entry_reference"]<long["targets"][0]["price"]
    assert short["targets"][0]["price"]<short["entry_reference"]<short["stop_loss"]

def test_structure_widens_stop():
    assert plan(swing_low=67000)["stop_loss"]<plan()["stop_loss"]

def test_margin_budget_caps_quantity():
    p=plan(margin_budget_fraction=.01)
    assert p["initial_margin_estimate"]<=p["margin_budget"]+1e-9
    assert p["position_sizing_limited_by"]=="margin_budget"

def test_leverage_reduces_when_atr_rises():
    assert plan(atr=1500)["leverage"]<=plan(atr=100)["leverage"]

def test_spot_is_unleveraged():
    p=plan(market_type="spot",max_leverage=1)
    assert p["leverage"]==1
    assert p["initial_margin_estimate"]==pytest.approx(p["notional"])

@pytest.mark.parametrize("kw",[{"signal_valid":False},{"data_integrity_valid":False},
    {"oos_authorized":False},{"deployment_gate":{"eligible":False}}])
def test_fail_closed(kw):
    p=plan(**kw)
    assert p["status"]=="NO_TRADE" and p["blockers"]

def test_costs_included_in_risk_budget():
    p=plan(fee_roundtrip_rate=.001,slippage_roundtrip_rate=.001)
    assert p["modeled_total_loss_at_stop"]<=p["risk_budget"]+1e-9
    assert p["modeled_roundtrip_cost"]>0

@pytest.mark.parametrize("kw",[{"account_equity":float("nan")},{"risk_fraction":.03},
    {"max_leverage":10},{"atr":0},{"side":"buy"},{"swing_low":0}])
def test_reject_unsafe_inputs(kw):
    with pytest.raises(ValueError): plan(**kw)

def test_targets_r_multiples_and_cost_block():
    p=plan(target_r_multiples=(2,3))
    assert [x["r_multiple"] for x in p["targets"]]==[2,3]
    assert plan(fee_roundtrip_rate=.2,slippage_roundtrip_rate=.04)["status"]=="NO_TRADE"

def test_blocks_when_even_one_x_cannot_preserve_liquidation_buffer():
    p=plan(atr=40000,stop_atr_multiple=1.5)
    assert p["leverage"]==1
    assert p["status"]=="NO_TRADE"
    assert any("Even 1x leverage" in reason for reason in p["blockers"])
