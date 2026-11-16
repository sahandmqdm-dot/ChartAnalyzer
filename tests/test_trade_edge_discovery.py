import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from trade_edge_discovery import analyze_trade_edge


def _trades(returns, side="long"):
    return [{"net_ret": x, "gross_account_ret": x + 0.001,
             "side": side, "market_regime": "trend", "entry_type": "pullback",
             "exit_reason": "target" if x > 0 else "stop"} for x in returns]


def test_empty_and_missing_returns_are_rejected_safely():
    report = analyze_trade_edge([{}, {"net_ret": None}], bootstrap_iterations=500)
    assert report["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert report["data_quality"]["excluded_missing_or_invalid_net_return"] == 2


def test_negative_edge_is_not_approved():
    report = analyze_trade_edge(_trades([-0.01, -0.02, 0.001] * 20), bootstrap_iterations=500)
    assert report["verdict"] == "NO_DEMONSTRATED_NET_EDGE"
    assert report["overall"]["net_expectancy_pct"] < 0


def test_strong_synthetic_edge_is_only_promising_not_live_approved():
    returns = [0.02, 0.015, 0.01, -0.004] * 30
    report = analyze_trade_edge(_trades(returns), min_samples=30, bootstrap_iterations=1000)
    assert report["verdict"] == "PROMISING_REQUIRES_WALK_FORWARD"
    assert report["status"] == "RESEARCH_ONLY"
    assert report["by_side"]["long"]["samples"] == len(returns)


def test_invalid_parameters_raise():
    with pytest.raises(ValueError):
        analyze_trade_edge([], min_samples=1)
    with pytest.raises(ValueError):
        analyze_trade_edge([], bootstrap_iterations=100)


def test_cost_stress_does_not_improve_expectancy():
    report = analyze_trade_edge(_trades([0.01, 0.02, -0.005] * 20), bootstrap_iterations=500)
    stress = report["overall"]["cost_stress"]
    assert stress["2.0"]["net_expectancy_pct"] <= stress["1.0"]["net_expectancy_pct"]
