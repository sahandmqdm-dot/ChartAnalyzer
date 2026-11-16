import numpy as np
from indicator_integrity import audit_indicators, METRIC_META


def _result(value=12.0, signal=0.4):
    return {
        "members": {"trend": {"ema": 1.0, "supertrend": 1.0}},
        "val": {"ema": np.array([10.0, value]), "supertrend": np.array([99.0, 101.0])},
        "sig": {"ema": np.array([0.1, signal]), "supertrend": np.array([0.2, 0.3])},
    }


def test_metric_semantics_distinguish_price_distance_from_price_level():
    assert METRIC_META["ema"][0] == "price_distance"
    assert METRIC_META["supertrend"][0] == "price_level"


def test_valid_indicator_outputs_pass_and_report_coverage():
    out = audit_indicators(_result(), [98.0, 100.0])
    assert out["state"] == "PASS"
    assert out["valid"] is True
    assert out["enabled_indicators"] == 2
    assert out["indicators"][0]["valid_coverage_pct"] == 100.0


def test_missing_latest_indicator_value_blocks_instead_of_accepting_neutral_fill():
    r = _result()
    r["val"]["ema"][-1] = np.nan
    out = audit_indicators(r, [98.0, 100.0])
    assert out["state"] == "BLOCKED"
    assert any("latest indicator value is unavailable" in x for x in out["errors"])


def test_price_level_scale_mismatch_is_blocked():
    r = _result()
    r["val"]["supertrend"][-1] = 100_000.0
    out = audit_indicators(r, [98.0, 100.0])
    assert out["state"] == "BLOCKED"
    assert any("price-scale mismatch" in x for x in out["errors"])


def test_oscillator_out_of_range_is_blocked():
    r = _result()
    r["members"] = {"momentum": {"rsi": 1.0}}
    r["val"] = {"rsi": np.array([50.0, 140.0])}
    r["sig"] = {"rsi": np.array([0.0, 0.5])}
    out = audit_indicators(r, [98.0, 100.0])
    assert not out["valid"]
    assert any("outside expected range" in x for x in out["errors"])


def test_signal_outside_normalized_bounds_is_blocked():
    r = _result(signal=1.5)
    out = audit_indicators(r, [98.0, 100.0])
    assert not out["valid"]
    assert any("signal contains" in x for x in out["errors"])


def test_expected_warmup_is_excluded_from_coverage_warning():
    # SMA has a 199-bar warm-up; its valid post-warm-up history should score 100%.
    r = {
        "members": {"trend": {"sma": 1.0}},
        "val": {"sma": np.array([np.nan] * 199 + [1.0] * 451)},
        "sig": {"sma": np.array([0.0] * 650)},
    }
    out = audit_indicators(r, [100.0] * 650)
    assert out["state"] == "PASS"
    assert out["indicators"][0]["warmup_bars"] == 199
    assert out["indicators"][0]["valid_coverage_pct"] == 100.0


def test_trade_plan_is_blocked_when_integrity_fails():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import app
    plan = {"action": "Long setup", "entry": [99.0, 100.0]}
    data = {"valid": True, "errors": []}
    indicators = {"valid": False, "errors": ["rsi: latest value unavailable"]}
    out = app.apply_integrity_gate(plan, data, indicators)
    assert out["action"] == "NO TRADE - DATA INTEGRITY BLOCK"
    assert "rsi" in out["note"]


def test_trade_plan_is_preserved_when_both_integrity_checks_pass():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import app
    plan = {"action": "Long setup"}
    assert app.apply_integrity_gate(plan, {"valid": True}, {"valid": True}) is plan
