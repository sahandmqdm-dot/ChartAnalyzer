"""Venue-aware execution cost accounting for research simulations.

Accounting-first: no limit-fill prediction, maker inference, or live execution.
Every supplied fill carries an explicit liquidity role and provenance.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any


ENGINE_VERSION = "ExecutionCostEngine 1.0"


def _number(value: Any, name: str, *, minimum: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    if minimum is not None and result < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return result


def _canonical_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_fee_schedule(schedule: dict[str, Any]) -> dict[str, Any]:
    """Validate venue/account rates and retain their provenance."""
    if not isinstance(schedule, dict):
        raise ValueError("fee_schedule is required")
    required = ("venue", "account_tier", "market", "maker_rate", "taker_rate",
                "source", "effective_at")
    missing = [key for key in required if schedule.get(key) in (None, "")]
    if missing:
        raise ValueError("missing fee schedule fields: " + ", ".join(missing))
    maker = _number(schedule["maker_rate"], "maker_rate", minimum=0.0)
    taker = _number(schedule["taker_rate"], "taker_rate", minimum=0.0)
    if maker >= 1 or taker >= 1:
        raise ValueError("fee rates must be less than 1")
    normalized = dict(schedule)
    normalized["maker_rate"] = maker
    normalized["taker_rate"] = taker
    normalized["schedule_hash"] = _canonical_hash({
        key: value for key, value in normalized.items() if key != "schedule_hash"
    })
    return normalized


def evaluate_execution(
    *,
    side: str,
    order_type: str,
    requested_quantity: float,
    fills: list[dict[str, Any]],
    fee_schedule: dict[str, Any],
    reference_mid: float,
    best_bid: float,
    best_ask: float,
    impact_bps: float | None = None,
    funding_rate: float | None = None,
    funding_events: int = 0,
    latency_ms: float | None = None,
    fill_source: str | None = None,
    assumptions: dict[str, Any] | None = None,
    rejection_reason: str | None = None,
    fill_probability: float | None = None,
    fill_probability_source: str | None = None,
    fill_probability_calibrated: bool = False,
) -> dict[str, Any]:
    """Account for supplied fills; never manufacture an execution."""
    side = str(side).lower()
    order_type = str(order_type).lower()
    if side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    if order_type not in {"market", "limit"}:
        raise ValueError("order_type must be market or limit")
    requested = _number(requested_quantity, "requested_quantity", minimum=0.0)
    if requested <= 0:
        raise ValueError("requested_quantity must be > 0")
    mid = _number(reference_mid, "reference_mid", minimum=0.0)
    bid = _number(best_bid, "best_bid", minimum=0.0)
    ask = _number(best_ask, "best_ask", minimum=0.0)
    if mid <= 0 or bid <= 0 or ask <= 0 or bid > ask:
        raise ValueError("valid positive reference_mid and best_bid <= best_ask are required")
    impact = None if impact_bps is None else _number(impact_bps, "impact_bps", minimum=0.0)
    funding = None if funding_rate is None else _number(funding_rate, "funding_rate")
    events = int(_number(funding_events, "funding_events", minimum=0.0))
    probability = None if fill_probability is None else _number(fill_probability, "fill_probability", minimum=0.0)
    if probability is not None and probability > 1:
        raise ValueError("fill_probability must be <= 1")
    if probability is not None and (not fill_probability_source or not fill_probability_calibrated):
        raise ValueError("fill probability requires calibrated model provenance")
    if rejection_reason and fills:
        raise ValueError("rejected order cannot contain fills")
    latency = None if latency_ms is None else _number(latency_ms, "latency_ms", minimum=0.0)
    schedule = validate_fee_schedule(fee_schedule)
    if not isinstance(fills, list):
        raise ValueError("fills must be a list")
    if fills and not fill_source:
        raise ValueError("fill_source is required when fills are supplied")

    parsed = []
    for index, fill in enumerate(fills):
        if not isinstance(fill, dict):
            raise ValueError(f"fill {index} must be an object")
        price = _number(fill.get("price"), f"fills[{index}].price", minimum=0.0)
        quantity = _number(fill.get("quantity"), f"fills[{index}].quantity", minimum=0.0)
        role = str(fill.get("liquidity_role", "")).lower()
        if price <= 0 or quantity <= 0:
            raise ValueError("fill price and quantity must be > 0")
        if role not in {"maker", "taker"}:
            raise ValueError("each fill must explicitly identify maker or taker liquidity")
        parsed.append({"price": price, "quantity": quantity, "liquidity_role": role})

    filled_qty = sum(row["quantity"] for row in parsed)
    tolerance = max(1e-12, requested * 1e-9)
    if filled_qty > requested + tolerance:
        raise ValueError("filled quantity cannot exceed requested quantity")
    status = "rejected" if rejection_reason else ("unfilled" if not parsed else (
        "partial" if filled_qty < requested - tolerance else "filled"
    ))
    gross_notional = sum(row["price"] * row["quantity"] for row in parsed)
    if gross_notional <= 0:
        vwap = None
        fee_cost = spread_cost = slippage_cost = impact_cost = funding_cost = total_cost = 0.0
        effective_cost_rate = None
    else:
        vwap = gross_notional / filled_qty
        fee_cost = sum(
            row["price"] * row["quantity"] * schedule[f'{row["liquidity_role"]}_rate']
            for row in parsed
        )
        touch = ask if side == "buy" else bid
        spread_cost = max(0.0, touch - mid if side == "buy" else mid - touch) * filled_qty
        slippage_cost = sum(
            max(0.0, row["price"] - touch if side == "buy" else touch - row["price"])
            * row["quantity"] for row in parsed
        )
        impact_cost = gross_notional * (impact or 0.0) / 10000.0
        funding_cost = gross_notional * (funding or 0.0) * events
        total_cost = fee_cost + spread_cost + slippage_cost + impact_cost + funding_cost
        effective_cost_rate = total_cost / gross_notional

    assumption_data = dict(assumptions or {})
    return {
        "engine": ENGINE_VERSION,
        "status": status,
        "side": side,
        "order_type": order_type,
        "requested_quantity": requested,
        "filled_quantity": filled_qty,
        "unfilled_quantity": max(0.0, requested - filled_qty),
        "fill_count": len(parsed),
        "vwap": vwap,
        "gross_filled_notional": gross_notional,
        "cost_breakdown": {
            "fees": fee_cost, "spread": spread_cost,
            "slippage_beyond_touch": slippage_cost, "market_impact": impact_cost,
            "funding": funding_cost, "total": total_cost,
            "effective_cost_rate": effective_cost_rate,
            "complete": impact is not None and (events == 0 or funding is not None),
            "missing_inputs": ([ "impact_bps" ] if impact is None else []) +
                ([ "funding_rate" ] if events > 0 and funding is None else []),
        },
        "execution_context": {
            "reference_mid": mid, "best_bid": bid, "best_ask": ask,
            "quoted_spread_bps": (ask - bid) / mid * 10000.0,
            "impact_bps_assumption": impact, "latency_ms": latency,
            "fill_source": fill_source, "assumptions": assumption_data,
            "fill_probability": probability,
            "fill_probability_source": fill_probability_source,
            "fill_probability_calibrated": bool(fill_probability_calibrated),
            "rejection_reason": rejection_reason,
        },
        "fee_schedule": schedule,
        "fills": parsed,
        "provenance": {
            "fill_source": fill_source,
            "fee_schedule_source": schedule["source"],
            "fee_schedule_effective_at": schedule["effective_at"],
            "fee_schedule_hash": schedule["schedule_hash"],
            "assumptions_hash": _canonical_hash(assumption_data),
        },
        "guardrails": [
            "No fill is inferred from order type or price touch.",
            "Maker/taker fees follow each fill's explicit liquidity_role.",
            "An unfilled order has no execution fee; opportunity cost is not trading PnL.",
            "Missing impact or applicable funding inputs make the cost breakdown incomplete.",
            "Fill probability is reported only when supplied by a calibrated, traceable external model.",
            "Research accounting only; no exchange order is submitted.",
        ],
    }


__all__ = ["ENGINE_VERSION", "evaluate_execution", "validate_fee_schedule"]
