import pytest

from datetime import UTC, datetime, timedelta

from decision_quality import DecisionQualityError, evaluate_decision_quality


T0 = datetime(2026, 1, 1, tzinfo=UTC)


def record(i, action, net, *, reason=None, start=None, end=None, resolved=None):
    start = start or T0 + timedelta(hours=i * 5)
    end = end or start + timedelta(hours=4)
    return {
        "decision_id": f"d{i}",
        "instrument_id": "BTCUSDT",
        "market_type": "crypto_perpetual",
        "timeframe": "1h",
        "horizon_bars": 4,
        "decision_at": start,
        "horizon_end": end,
        "resolved_at": resolved or end,
        "action": action,
        "reason_code": reason or ("EDGE_INSUFFICIENT" if action == "NO_TRADE" else "EDGE_CONFIRMED"),
        "counterfactual_net_return_if_traded": net,
    }


def test_abstention_value_compared_with_forced_action_counterfactual():
    rows = [
        record(0, "ACT", 0.02),
        record(1, "NO_TRADE", -0.03),
        record(2, "NO_TRADE", 0.01),
        record(3, "ACT", -0.01),
    ]
    result = evaluate_decision_quality(rows)
    assert result["status"] == "DESCRIPTIVE_ONLY"
    assert result["sample"]["action_coverage"] == 0.5
    assert result["abstention"]["avoided_loss_count"] == 1
    assert result["abstention"]["avoided_loss_return_sum"] == pytest.approx(0.03)
    assert result["abstention"]["missed_positive_opportunity_count"] == 1
    assert result["abstention"]["missed_positive_return_sum"] == pytest.approx(0.01)
    assert result["policy"]["unit_notional_net_return_sum"] == pytest.approx(0.01)
    assert result["forced_action_counterfactual"]["unit_notional_net_return_sum"] == pytest.approx(-0.01)
    assert result["abstention"]["net_incremental_value_vs_forced_action"] == pytest.approx(0.02)
    assert result["evaluation_contract"]["causal_claim"] is False


def test_requires_outcome_for_no_trade_and_timezone_aware_timestamps():
    row = record(0, "NO_TRADE", -0.01)
    row["counterfactual_net_return_if_traded"] = None
    with pytest.raises(DecisionQualityError):
        evaluate_decision_quality([row])
    row = record(0, "NO_TRADE", -0.01)
    row["decision_at"] = datetime(2026, 1, 1)
    with pytest.raises(DecisionQualityError, match="timezone-aware"):
        evaluate_decision_quality([row])


def test_rejects_overlapping_horizons_and_duplicate_ids():
    first = record(0, "ACT", 0.01)
    second = record(
        1, "NO_TRADE", -0.01,
        start=first["decision_at"] + timedelta(hours=2),
    )
    with pytest.raises(DecisionQualityError, match="overlapping"):
        evaluate_decision_quality([first, second])
    duplicate = record(0, "ACT", 0.01)
    with pytest.raises(DecisionQualityError, match="unique"):
        evaluate_decision_quality([first, duplicate])


def test_rejects_mixed_cohorts_and_early_resolution():
    first = record(0, "ACT", 0.01)
    second = record(1, "NO_TRADE", -0.01)
    second["timeframe"] = "4h"
    with pytest.raises(DecisionQualityError, match="one instrument"):
        evaluate_decision_quality([first, second])
    bad = record(0, "ACT", 0.01, resolved=T0 + timedelta(hours=1))
    with pytest.raises(DecisionQualityError, match="cannot resolve"):
        evaluate_decision_quality([bad])


def test_rejects_nonfinite_return_and_missing_reason_code():
    bad = record(0, "ACT", float("nan"))
    with pytest.raises(DecisionQualityError, match="finite"):
        evaluate_decision_quality([bad])
    bad = record(0, "NO_TRADE", 0.01, reason=" ")
    with pytest.raises(DecisionQualityError, match="reason_code"):
        evaluate_decision_quality([bad])
