"""Venue-aware execution cost accounting for research simulations.

Accounting-first: no limit-fill prediction, maker inference, or live execution.
Every supplied fill carries an explicit liquidity role and provenance.
"""
from __future__ import annotations  # noqa: I001

import datetime
import hashlib
import json
import math
from typing import Any


ENGINE_VERSION = "ExecutionCostEngine 1.0"


def _number(value: Any, name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{name} must be numeric, not boolean")
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
        raise TypeError("fee_schedule must be an object")
    required = ("venue", "account_tier", "market", "maker_rate", "taker_rate",
                "source", "effective_at")
    missing = [key for key in required if schedule.get(key) in (None, "")]
    if missing:
        raise ValueError("missing fee schedule fields: " + ", ".join(missing))
    effective_at = schedule["effective_at"]
    if not isinstance(effective_at, str):
        raise ValueError("fee schedule effective_at must be an ISO-8601 string")
    try:
        effective_dt = datetime.datetime.fromisoformat(effective_at)
    except ValueError as exc:
        raise ValueError("fee schedule effective_at must be an ISO-8601 string") from exc
    if effective_dt.tzinfo is None or effective_dt.utcoffset() is None:
        raise ValueError("fee schedule effective_at must include a timezone")
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
    impact_source: str | None = None,
    funding_rate: float | None = None,
    funding_source: str | None = None,
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
    events_value = _number(funding_events, "funding_events", minimum=0.0)
    if not events_value.is_integer():
        raise ValueError("funding_events must be an integer")
    events = int(events_value)
    probability = None if fill_probability is None else _number(fill_probability, "fill_probability", minimum=0.0)
    if impact_source is not None and not str(impact_source).strip():
        raise ValueError("impact_source must be non-empty when supplied")
    if funding_source is not None and not str(funding_source).strip():
        raise ValueError("funding_source must be non-empty when supplied")
    if probability is not None and probability > 1:
        raise ValueError("fill_probability must be <= 1")
    if probability is not None and order_type != "limit":
        raise ValueError("fill_probability applies only to limit orders")
    if probability is not None and (not fill_probability_source or not fill_probability_calibrated):
        raise ValueError("fill probability requires calibrated model provenance")
    if rejection_reason and fills:
        raise ValueError("rejected order cannot contain fills")
    if rejection_reason and not str(rejection_reason).strip():
        raise ValueError("rejection_reason must be non-empty")
    latency = None if latency_ms is None else _number(latency_ms, "latency_ms", minimum=0.0)
    schedule = validate_fee_schedule(fee_schedule)
    if not isinstance(fills, list):
        raise TypeError("fills must be a list")
    if fills and not fill_source:
        raise ValueError("fill_source is required when fills are supplied")

    parsed = []
    for index, fill in enumerate(fills):
        if not isinstance(fill, dict):
            raise TypeError(f"fill {index} must be an object")
        price = _number(fill.get("price"), f"fills[{index}].price", minimum=0.0)
        quantity = _number(fill.get("quantity"), f"fills[{index}].quantity", minimum=0.0)
        role = str(fill.get("liquidity_role", "")).lower()
        if price <= 0 or quantity <= 0:
            raise ValueError("fill price and quantity must be > 0")
        if role not in {"maker", "taker"}:
            raise ValueError("each fill must explicitly identify maker or taker liquidity_role")
        if order_type == "market" and role != "taker":
            raise ValueError("market-order fills must be taker liquidity")
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
            "complete": (impact is not None and bool(impact_source)
                         and (events == 0 or (funding is not None and bool(funding_source)))),
            "missing_inputs": (["impact_bps"] if impact is None else []) +
                (["impact_source"] if not impact_source else []) +
                (["funding_rate"] if events > 0 and funding is None else []) +
                (["funding_source"] if events > 0 and not funding_source else []),
        },
        "execution_context": {
            "reference_mid": mid, "best_bid": bid, "best_ask": ask,
            "quoted_spread_bps": (ask - bid) / mid * 10000.0,
            "impact_bps_assumption": impact, "impact_source": impact_source,
            "funding_source": funding_source, "latency_ms": latency,
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
            "impact_source": impact_source,
            "funding_source": funding_source,
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


def market_sweep_fills(
    book: dict[str, Any],
    *,
    side: str,
    requested_quantity: float,
    book_received_at: str,
    decision_at: str,
    max_age_ms: float = 500.0,
) -> dict[str, Any]:
    """Build taker fills by sweeping a fresh, sequence-valid L2 snapshot.

    Timestamps are local receive/decision times, not exchange event times.
    This is a snapshot liquidity estimate, not a queue-aware historical fill.
    """
    if not isinstance(book, dict):
        raise TypeError("book must be an object")
    if book.get("valid") is not True:
        raise ValueError("a sequence-valid order book is required")
    if not book.get("market") or not book.get("symbol"):
        raise ValueError("book market and symbol identity are required")
    if isinstance(book.get("last_update_id"), bool) or not isinstance(book.get("last_update_id"), int):
        raise TypeError("book last_update_id must be an integer")
    if book["last_update_id"] < 0:
        raise ValueError("book last_update_id must be >= 0")
    if book.get("market") not in {"spot", "usdm"}:
        raise ValueError("book market must be spot or usdm")
    if not isinstance(book.get("symbol"), str) or not book["symbol"].strip():
        raise ValueError("book symbol must be a non-empty string")
    side = str(side).lower()
    if side not in {"buy", "sell"}:
        raise ValueError("side must be buy or sell")
    requested = _number(requested_quantity, "requested_quantity", minimum=0.0)
    if requested <= 0:
        raise ValueError("requested_quantity must be > 0")
    age_limit = _number(max_age_ms, "max_age_ms", minimum=0.0)
    try:
        received = datetime.datetime.fromisoformat(book_received_at)
        decision = datetime.datetime.fromisoformat(decision_at)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("book and decision times must be ISO-8601") from exc
    if received.tzinfo is None or decision.tzinfo is None:
        raise ValueError("book and decision times must include a timezone")
    age_ms = (decision - received).total_seconds() * 1000.0
    if age_ms < 0:
        raise ValueError("book receive time cannot be after the decision time")
    if age_ms > age_limit:
        raise ValueError("order book is stale for the configured decision-time limit")

    levels = book.get("asks" if side == "buy" else "bids")
    if not isinstance(levels, (list, tuple)) or not levels:
        raise ValueError("required side of the order book is empty")
    parsed_levels = []
    for level in levels:
        if not isinstance(level, (list, tuple)) or len(level) != 2:
            raise ValueError("malformed order-book level")
        price = _number(level[0], "book level price", minimum=0.0)
        quantity = _number(level[1], "book level quantity", minimum=0.0)
        if price <= 0 or quantity <= 0:
            raise ValueError("book level price and quantity must be > 0")
        parsed_levels.append((price, quantity))
    expected = sorted(parsed_levels, key=lambda row: row[0], reverse=(side == "sell"))
    if parsed_levels != expected:
        raise ValueError("book levels must be sorted by executable price")
    if len({price for price, _ in parsed_levels}) != len(parsed_levels):
        raise ValueError("book levels must not contain duplicate prices")
    opposite = book.get("bids" if side == "buy" else "asks")
    if not isinstance(opposite, (list, tuple)) or not opposite:
        raise ValueError("opposite side of the order book is empty")
    opposite_levels = []
    for row in opposite:
        if not isinstance(row, (list, tuple)) or len(row) != 2:
            raise ValueError("malformed opposite order-book side")
        opposite_price = _number(row[0], "opposite book price", minimum=0.0)
        opposite_quantity = _number(row[1], "opposite book quantity", minimum=0.0)
        if opposite_price <= 0 or opposite_quantity <= 0:
            raise ValueError("opposite book prices and quantities must be > 0")
        opposite_levels.append((opposite_price, opposite_quantity))
    opposite_prices = [price for price, _ in opposite_levels]
    if len(set(opposite_prices)) != len(opposite_prices):
        raise ValueError("opposite book levels must not contain duplicate prices")
    expected_opposite = sorted(opposite_prices, reverse=(side == "buy"))
    if opposite_prices != expected_opposite:
        raise ValueError("opposite book levels must be sorted by executable price")
    if side == "buy" and max(opposite_prices) >= min(price for price, _ in parsed_levels):
        raise ValueError("crossed order book")
    if side == "sell" and max(price for price, _ in parsed_levels) >= min(opposite_prices):
        raise ValueError("crossed order book")

    fills = []
    remaining = requested
    for price, available in parsed_levels:
        if remaining <= 0:
            break
        quantity = min(remaining, available)
        fills.append({"price": price, "quantity": quantity, "liquidity_role": "taker"})
        remaining = max(0.0, remaining - quantity)
    return {
        "fills": fills,
        "fill_source": "sequence_validated_l2_snapshot_sweep",
        "book_provenance": {
            "market": str(book["market"]),
            "symbol": str(book["symbol"]).upper(),
            "last_update_id": book["last_update_id"],
            "received_at": received.isoformat(),
            "decision_at": decision.isoformat(),
            "age_ms": age_ms,
            "max_age_ms": age_limit,
        },
        "requested_quantity": requested,
        "estimated_filled_quantity": sum(fill["quantity"] for fill in fills),
        "estimated_unfilled_quantity": remaining,
        "is_full_depth_fill": remaining <= 1e-12,
        "limitations": [
            "Snapshot sweep is a point-in-time displayed-depth estimate.",
            "It excludes queue position, hidden liquidity, cancellations and intervening events.",
            "It must not be described as a historical realized fill.",
        ],
    }


def record_execution_cost_experiment(
    registry: Any,
    *,
    experiment_id: str,
    hypothesis_id: str,
    dataset_id: str,
    config_hash: str,
    source: str,
    symbol: str,
    timeframe: str,
    execution_result: dict[str, Any],
    run_id: str | None = None,
    sample_start: float | None = None,
    sample_end: float | None = None,
) -> dict[str, Any]:
    """Persist cost evidence through the existing append-only ResearchRegistry."""
    if not isinstance(execution_result, dict):
        raise TypeError("execution_result must be an object")
    if execution_result.get("engine") != ENGINE_VERSION:
        raise ValueError("execution_result must come from this execution cost engine")
    breakdown = execution_result.get("cost_breakdown")
    provenance = execution_result.get("provenance")
    if not isinstance(breakdown, dict) or not isinstance(provenance, dict):
        raise TypeError("execution result is missing object cost breakdown or provenance")
    complete = breakdown.get("complete") is True
    metrics = {
        "execution_cost_engine": ENGINE_VERSION,
        "execution_status": execution_result.get("status"),
        "cost_model_complete": complete,
        "cost_breakdown": breakdown,
        "execution_context": execution_result.get("execution_context", {}),
        "fee_schedule_provenance": execution_result.get("provenance", {}),
        "fills": execution_result.get("fills", []),
        "requested_quantity": execution_result.get("requested_quantity"),
        "filled_quantity": execution_result.get("filled_quantity"),
        "unfilled_quantity": execution_result.get("unfilled_quantity"),
    }
    recorded = registry.record(
        experiment_id=experiment_id,
        hypothesis_id=hypothesis_id,
        dataset_id=dataset_id,
        config_hash=config_hash,
        source=source,
        symbol=symbol,
        timeframe=timeframe,
        metrics=metrics,
        p_value=None,
        status="COMPLETED" if complete else "INCOMPLETE_COST_MODEL",
        run_id=run_id,
        sample_start=sample_start,
        sample_end=sample_end,
    )
    return {**recorded, "cost_model_complete": complete,
            "registry_status": "COMPLETED" if complete else "INCOMPLETE_COST_MODEL"}


__all__ = ["ENGINE_VERSION", "evaluate_execution", "market_sweep_fills", "record_execution_cost_experiment", "validate_fee_schedule"]
