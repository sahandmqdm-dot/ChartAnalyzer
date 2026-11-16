import pytest

from entry_edge_research import analyze_entry_edge


def test_entry_edge_uses_direction_adjusted_scores_and_realized_net_returns():
    trades = []
    for i in range(24):
        if i < 8:
            trades.append({"side": "long", "entry_signal_score": 0.25, "net_ret": 0.002})
        elif i < 16:
            trades.append({"side": "long", "entry_signal_score": 0.42, "net_ret": -0.001})
        else:
            trades.append({"side": "short", "entry_signal_score": -0.65, "net_ret": 0.001})
    out = analyze_entry_edge(trades, min_samples=6, bootstrap_iterations=300)
    assert out["scope"] == "sequential_walk_forward_oos_executed_trades_only"
    assert out["samples"] == 24
    assert out["directional_score_cohorts"]["near_threshold"]["samples"] == 8
    assert out["directional_score_cohorts"]["intermediate"]["samples"] == 8
    assert out["directional_score_cohorts"]["high_conviction"]["samples"] == 8
    assert out["overall"]["bootstrap_ci_95_pct"] is not None
    assert out["directional_score_cohorts"]["high_conviction"]["net_expectancy_pct"] == pytest.approx(0.1)


def test_entry_edge_fails_closed_for_missing_scores_and_returns():
    out = analyze_entry_edge([{"side": "long", "entry_signal_score": 0.3}])
    assert out["samples"] == 0
    assert out["invalid_or_missing"] == 1
    assert out["status"] == "INSUFFICIENT_DATA"


def test_entry_edge_rejects_invalid_configuration():
    with pytest.raises(ValueError):
        analyze_entry_edge([], min_samples=1)
    with pytest.raises(ValueError):
        analyze_entry_edge([], bootstrap_iterations=10)
