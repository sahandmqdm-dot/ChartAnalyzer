from strategy_validation import (
    build_evidence_record,
    evaluate_evidence,
    evaluate_strategy_lifecycle,
)


def _result():
    return {
        "entry_edge_research": {"overall": {"bootstrap_ci_95_pct": [0.08, 0.42]}},
        "folds": [{"strategy_pct": 1.2}, {"strategy_pct": 0.4}, {"strategy_pct": 0.8},
                  {"strategy_pct": -0.1}, {"strategy_pct": 0.3}],
        "data_integrity": {"valid": True, "gap_count": 0},
        "research_cohort_integrity": {"status": "PASS"},
        "strategy_frozen": True,
        "parameter_optimization": False,
        "trades": 240,
        "net_expectancy_pct": 0.15,
        "net_profit_factor": 1.24,
        "max_dd_pct": -8.0,
        "research_inference": {"p_value": 0.01, "bootstrap_ci_95_pct": [0.08, 0.42]},
        "adjusted_p_value": 0.02,
        "cost_stress_1_5x_positive": True,
    }


def test_evidence_decision_class_distinguishes_no_edge_from_insufficient_sample():
    result = _result()
    result.update(net_expectancy_pct=-0.02, net_profit_factor=0.98)
    result["research_inference"]["bootstrap_ci_95_pct"] = [-0.08, -0.01]
    record = build_evidence_record(result, source="ramzinex", symbol="USDTIRR",
                                   timeframe="1h", config_hash="a" * 64, created_at=1000)
    decision = evaluate_evidence(record, now=1001)
    assert decision["decision_class"] == "NO_EDGE"
    assert decision["eligible"] is False

    # Negative point estimates without a fully negative confidence interval
    # are inconclusive, not proof that the strategy has no edge.
    uncertain = _result()
    uncertain.update(net_expectancy_pct=-0.02, net_profit_factor=0.98)
    uncertain["research_inference"]["bootstrap_ci_95_pct"] = [-0.08, 0.01]
    uncertain_record = build_evidence_record(
        uncertain, source="ramzinex", symbol="USDTIRR", timeframe="1h",
        config_hash="e" * 64, created_at=1000,
    )
    assert evaluate_evidence(uncertain_record, now=1001)["decision_class"] == "UNVERIFIED"

    result = _result()
    result["trades"] = 42
    record = build_evidence_record(result, source="ramzinex", symbol="USDTIRR",
                                   timeframe="1h", config_hash="b" * 64, created_at=1000)
    decision = evaluate_evidence(record, now=1001)
    assert decision["decision_class"] == "INSUFFICIENT_EVIDENCE"

    stale = evaluate_evidence(
        build_evidence_record(_result() | {"net_expectancy_pct": -0.1, "net_profit_factor": 0.8},
                              source="ramzinex", symbol="USDTIRR", timeframe="1h",
                              config_hash="f" * 64, created_at=1000),
        now=1000 + 25 * 3600,
    )
    assert stale["decision_class"] == "UNVERIFIED"


def test_invalid_data_has_precedence_over_performance_classification():
    result = _result()
    result["data_integrity"]["valid"] = False
    record = build_evidence_record(result, source="ramzinex", symbol="USDTIRR",
                                   timeframe="1h", config_hash="c" * 64, created_at=1000)
    assert evaluate_evidence(record, now=1001)["decision_class"] == "DATA_INVALID"


def _bad_window(start):
    return {
        "start_at": start, "end_at": start + 100, "window_sequence": int(start / 100) + 1,\n        "window_id": f"window-{int(start / 100) + 1}", "config_fingerprint": "same-frozen-config",
        "oos_trades": 240, "net_expectancy_pct": -0.12,
        "net_profit_factor": 0.82, "bootstrap_ci_95_pct": [-0.20, -0.04],
        "data_integrity_valid": True, "cohort_integrity": "PASS",
        "strategy_frozen": True, "parameter_optimization": False,
    }


def test_strategy_retirement_requires_two_disjoint_negative_oos_windows():
    one = evaluate_strategy_lifecycle([_bad_window(0)])
    assert one["lifecycle"] == "WATCH"

    two = evaluate_strategy_lifecycle([_bad_window(0), _bad_window(100)])
    assert two["lifecycle"] == "RETIRE_CANDIDATE"
    assert two["automatic_deactivation"] is False


def test_strategy_is_not_retired_on_one_positive_or_overlapping_window():
    positive = _bad_window(100)
    positive.update(net_expectancy_pct=0.1, net_profit_factor=1.1,
                    bootstrap_ci_95_pct=[0.01, 0.2])
    assert evaluate_strategy_lifecycle([_bad_window(0), positive])["lifecycle"] == "ACTIVE_REVIEW"
    assert evaluate_strategy_lifecycle([_bad_window(0), _bad_window(90)])["lifecycle"] == "WATCH"\n    skipped = _bad_window(200)\n    assert evaluate_strategy_lifecycle([_bad_window(0), skipped])["lifecycle"] == "WATCH"
