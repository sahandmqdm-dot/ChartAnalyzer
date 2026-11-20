"""Decision-quality and abstention diagnostics for resolved, non-overlapping opportunities.

This module evaluates whether a frozen decision policy's ACT/NO_TRADE choices
added value relative to a forced-action counterfactual. It is descriptive only:
it does not estimate causal policy value under overlapping capital, market
impact, capacity constraints, or portfolio interactions.
"""
from __future__ import annotations

import math
from itertools import pairwise
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any


class DecisionQualityError(ValueError):
    """A decision record violates the point-in-time evaluation contract."""


def _timestamp(value: Any, field: str) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise DecisionQualityError(f"{field} must be an ISO-8601 timestamp") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DecisionQualityError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _finite(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise DecisionQualityError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise DecisionQualityError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise DecisionQualityError(f"{field} must be finite")
    return result


def evaluate_decision_quality(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Score one homogeneous cohort of resolved, non-overlapping decisions.

    Required record fields:
      decision_id, instrument_id, market_type, timeframe, horizon_bars,
      decision_at, horizon_end, resolved_at, action, reason_code,
      counterfactual_net_return_if_traded

    Returns are decimal account returns after all modeled costs for a fixed
    unit-notional hypothetical trade. A NO_TRADE decision has policy return 0.
    Every opportunity must have a resolved counterfactual outcome, including
    NO_TRADE opportunities, otherwise abstention quality is not measurable.
    """
    rows = list(records)
    if not rows:
        raise DecisionQualityError("at least one resolved decision is required")

    normalized = []
    seen_ids: set[str] = set()
    cohort_fields = ("instrument_id", "market_type", "timeframe", "horizon_bars")
    cohort = None
    for raw in rows:
        if not isinstance(raw, dict):
            raise DecisionQualityError("each decision must be a mapping")
        decision_id = str(raw.get("decision_id", "")).strip()
        if not decision_id or decision_id in seen_ids:
            raise DecisionQualityError("decision_id must be non-empty and unique")
        seen_ids.add(decision_id)
        identity = tuple(str(raw.get(k, "")).strip() for k in cohort_fields)
        if any(not x for x in identity[:3]) or not identity[3]:
            raise DecisionQualityError("cohort identity fields must be non-empty")
        if cohort is None:
            cohort = identity
        elif identity != cohort:
            raise DecisionQualityError("evaluate one instrument/market/timeframe/horizon cohort at a time")
        horizon = raw.get("horizon_bars")
        if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < 1:
            raise DecisionQualityError("horizon_bars must be a positive integer")

        decision_at = _timestamp(raw.get("decision_at"), "decision_at")
        horizon_end = _timestamp(raw.get("horizon_end"), "horizon_end")
        resolved_at = _timestamp(raw.get("resolved_at"), "resolved_at")
        if horizon_end <= decision_at:
            raise DecisionQualityError("horizon_end must be after decision_at")
        if resolved_at < horizon_end:
            raise DecisionQualityError("counterfactual outcome cannot resolve before horizon_end")

        action = str(raw.get("action", "")).strip().upper()
        if action not in {"ACT", "NO_TRADE"}:
            raise DecisionQualityError("action must be ACT or NO_TRADE")
        reason = str(raw.get("reason_code", "")).strip()
        if not reason:
            raise DecisionQualityError("reason_code is required for every decision")
        counterfactual = _finite(
            raw.get("counterfactual_net_return_if_traded"),
            "counterfactual_net_return_if_traded",
        )
        normalized.append({
            "decision_id": decision_id,
            "decision_at": decision_at,
            "horizon_end": horizon_end,
            "resolved_at": resolved_at,
            "action": action,
            "reason_code": reason,
            "counterfactual": counterfactual,
        })

    normalized.sort(key=lambda x: x["decision_at"])
    for previous, current in pairwise(normalized):
        if current["decision_at"] < previous["horizon_end"]:
            raise DecisionQualityError(
                "overlapping opportunity horizons are not comparable in this single-unit diagnostic"
            )
        if current["decision_at"] == previous["decision_at"]:
            raise DecisionQualityError("decision timestamps must be unique")

    n = len(normalized)
    acted = [r for r in normalized if r["action"] == "ACT"]
    abstained = [r for r in normalized if r["action"] == "NO_TRADE"]
    policy_returns = [r["counterfactual"] if r["action"] == "ACT" else 0.0 for r in normalized]
    forced_returns = [r["counterfactual"] for r in normalized]
    avoided_losses = [r["counterfactual"] for r in abstained if r["counterfactual"] < 0]
    missed_gains = [r["counterfactual"] for r in abstained if r["counterfactual"] > 0]
    acted_returns = [r["counterfactual"] for r in acted]

    def total(values: list[float]) -> float:
        return float(sum(values))

    def mean(values: list[float]) -> float | None:
        return total(values) / len(values) if values else None

    reason_counts = dict(Counter(r["reason_code"] for r in normalized))
    return {
        "schema": "chartanalyzer_decision_quality_v1",
        "status": "DESCRIPTIVE_ONLY",
        "cohort": {
            "instrument_id": cohort[0],
            "market_type": cohort[1],
            "timeframe": cohort[2],
            "horizon_bars": int(cohort[3]),
        },
        "sample": {
            "decisions": n,
            "acted": len(acted),
            "abstained": len(abstained),
            "action_coverage": len(acted) / n,
            "abstention_rate": len(abstained) / n,
        },
        "policy": {
            "unit_notional_net_return_sum": total(policy_returns),
            "mean_return_per_opportunity": mean(policy_returns),
            "mean_return_per_acted_opportunity": mean(acted_returns),
            "positive_acted_opportunities": sum(x > 0 for x in acted_returns),
            "negative_acted_opportunities": sum(x < 0 for x in acted_returns),
        },
        "forced_action_counterfactual": {
            "unit_notional_net_return_sum": total(forced_returns),
            "mean_return_per_opportunity": mean(forced_returns),
        },
        "abstention": {
            "avoided_loss_count": len(avoided_losses),
            "avoided_loss_return_sum": -total(avoided_losses),
            "missed_positive_opportunity_count": len(missed_gains),
            "missed_positive_return_sum": total(missed_gains),
            "net_incremental_value_vs_forced_action": total(policy_returns) - total(forced_returns),
        },
        "reason_code_counts": reason_counts,
        "evaluation_contract": {
            "return_basis": "counterfactual net account return after modeled costs, fixed unit notional",
            "overlap_policy": "overlapping opportunity horizons rejected",
            "outcome_requirement": "every ACT and NO_TRADE opportunity must have a resolved counterfactual",
            "causal_claim": False,
            "portfolio_claim": False,
        },
        "limitations": [
            "descriptive paired counterfactual only; no causal policy-value claim",
            "assumes one unit-notional opportunity at a time and does not model capital compounding",
            "does not model market impact, capacity, portfolio interactions, or execution uncertainty beyond supplied net returns",
            "selection thresholds and reason codes must be frozen before prospective evaluation",
            "confirmatory use requires a prospective untouched cohort and dependence-aware confidence intervals",
        ],
    }


__all__ = ["DecisionQualityError", "evaluate_decision_quality"]
