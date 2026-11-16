import pytest

from exit_policy_research import analyze_exit_policy_research


def test_paired_exit_research_compares_same_trade_cohort_and_bootstraps_delta():
    trades = [
        {
            "net_account_return_pct": -0.5,
            "counterfactual_early_time_exits": [
                {"horizon_bars_requested": 2, "estimated_net_account_return_pct": 0.1},
            ],
        },
        {
            "net_account_return_pct": -0.2,
            "counterfactual_early_time_exits": [
                {"horizon_bars_requested": 2, "estimated_net_account_return_pct": -0.1},
            ],
        },
        {
            "net_account_return_pct": 0.1,
            "counterfactual_early_time_exits": [
                {"horizon_bars_requested": 2, "estimated_net_account_return_pct": 0.2},
            ],
        },
    ]
    out = analyze_exit_policy_research(trades, min_samples=2, bootstrap_iterations=300)
    h = out["horizons"]["2"]
    assert h["samples"] == 3
    assert h["paired_actual_mean_net_pct"] == pytest.approx(-0.2)
    assert h["counterfactual_mean_net_pct"] == pytest.approx(0.06667, abs=1e-5)
    assert h["paired_mean_net_delta_pct"] == pytest.approx(0.26667, abs=1e-5)
    assert h["status"] == "RESEARCH_SAMPLE_THRESHOLD_MET"
    assert len(h["paired_delta_bootstrap_95_ci_pct"]) == 2


def test_invalid_or_missing_counterfactuals_fail_closed():
    out = analyze_exit_policy_research([{"net_ret": "bad", "counterfactual_early_time_exits": []}])
    assert out["status"] == "NO_COUNTERFACTUAL_SAMPLES"
    assert out["invalid_trades"] == 1


def test_rejects_unreasonable_parameters():
    with pytest.raises(ValueError):
        analyze_exit_policy_research([], min_samples=1)
    with pytest.raises(ValueError):
        analyze_exit_policy_research([], bootstrap_iterations=10)
