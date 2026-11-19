"""Exchange-filter validation for linear futures trade plans.

This module consumes normalized contract metadata supplied by an exchange adapter.
It deliberately contains no exchange-specific constants and never submits orders.
Unknown or incomplete contract rules fail closed.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_UP
from typing import Any, Dict, Iterable


def _decimal(name: str, value: Any, *, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite decimal") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'finite'}")
    return result


def _floor_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _ceil_step(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_UP) * step


def _as_float(value: Decimal) -> float:
    return float(value)


def validate_futures_rules(rules: Dict[str, Any]) -> Dict[str, Any]:
    """Validate normalized linear-contract metadata from a trusted exchange adapter.

    Required keys:
      symbol, status='TRADING', contract_type='linear', quote_asset,
      tick_size, step_size, min_qty, min_notional, max_qty, max_leverage,
      maintenance_margin_tiers=[{notional_cap, maintenance_margin_rate,
      max_leverage}, ...] sorted by increasing notional_cap.
    """
    if not isinstance(rules, dict):
        raise ValueError("exchange futures rules must be a mapping")
    required = ("symbol", "status", "contract_type", "quote_asset", "tick_size",
                "step_size", "min_qty", "min_notional", "max_qty", "max_leverage",
                "maintenance_margin_tiers")
    missing = [key for key in required if key not in rules or rules[key] is None]
    if missing:
        raise ValueError("missing exchange futures rules: " + ", ".join(missing))
    if str(rules["status"]).upper() != "TRADING":
        raise ValueError("contract is not in TRADING status")
    if str(rules["contract_type"]).lower() != "linear":
        raise ValueError("only linear quote-settled futures contracts are supported")
    if not str(rules["symbol"]).strip() or not str(rules["quote_asset"]).strip():
        raise ValueError("symbol and quote_asset are required")

    for key in ("tick_size", "step_size", "min_qty", "min_notional", "max_qty", "max_leverage"):
        _decimal(key, rules[key], positive=True)
    if _decimal("min_qty", rules["min_qty"]) > _decimal("max_qty", rules["max_qty"]):
        raise ValueError("min_qty cannot exceed max_qty")
    if _decimal("max_leverage", rules["max_leverage"]) < 1:
        raise ValueError("max_leverage must be at least 1x")

    tiers = rules["maintenance_margin_tiers"]
    if not isinstance(tiers, list) or not tiers:
        raise ValueError("maintenance_margin_tiers must be a non-empty list")
    previous_cap = Decimal("0")
    for index, tier in enumerate(tiers):
        if not isinstance(tier, dict):
            raise ValueError(f"maintenance tier {index} must be a mapping")
        cap = _decimal(f"tier[{index}].notional_cap", tier.get("notional_cap"), positive=True)
        mmr = _decimal(f"tier[{index}].maintenance_margin_rate", tier.get("maintenance_margin_rate"))
        lev = _decimal(f"tier[{index}].max_leverage", tier.get("max_leverage"), positive=True)
        if cap <= previous_cap:
            raise ValueError("maintenance tiers must have strictly increasing notional caps")
        if mmr < 0 or mmr >= 1:
            raise ValueError("maintenance margin rate must be in [0, 1)")
        if lev < 1 or lev > _decimal("max_leverage", rules["max_leverage"]):
            raise ValueError("tier max leverage must be between 1x and contract max leverage")
        previous_cap = cap
    return rules


def _aligned(value: Decimal, step: Decimal) -> bool:
    return value % step == 0


def apply_futures_filters(plan: Dict[str, Any], rules: Dict[str, Any]) -> Dict[str, Any]:
    """Apply quantity, tick, notional, leverage and maintenance-tier filters.

    Quantity is rounded down. Long stops are rounded up and long targets down;
    short stops are rounded down and short targets up, avoiding risk expansion.
    The result remains READY_FOR_REVIEW and actionable=False. No order is sent.
    """
    validate_futures_rules(rules)
    if not isinstance(plan, dict):
        raise ValueError("trade plan must be a mapping")
    if str(plan.get("market_type", "")).lower() != "futures":
        raise ValueError("futures filters cannot be applied to a non-futures plan")
    if plan.get("status") != "READY_FOR_REVIEW":
        return {"status": "NO_TRADE", "actionable": False,
                "blockers": ["Only READY_FOR_REVIEW plans can be checked against exchange filters."]}
    side = str(plan.get("side", "")).lower()
    if side not in {"long", "short"}:
        raise ValueError("plan side must be long or short")

    entry = _decimal("entry_reference", plan.get("entry_reference"), positive=True)
    qty_raw = _decimal("quantity_base", plan.get("quantity_base"), positive=True)
    leverage = _decimal("leverage", plan.get("leverage"), positive=True)
    stop_raw = _decimal("stop_loss", plan.get("stop_loss"), positive=True)
    tick = _decimal("tick_size", rules["tick_size"], positive=True)
    step = _decimal("step_size", rules["step_size"], positive=True)
    qty = _floor_step(qty_raw, step)
    stop = _ceil_step(stop_raw, tick) if side == "long" else _floor_step(stop_raw, tick)
    blockers = []

    if qty < _decimal("min_qty", rules["min_qty"]):
        blockers.append("Quantity is below exchange minimum after step-size rounding.")
    if qty > _decimal("max_qty", rules["max_qty"]):
        blockers.append("Quantity exceeds exchange maximum.")
    notional = qty * entry
    if notional < _decimal("min_notional", rules["min_notional"]):
        blockers.append("Order notional is below exchange minimum.")
    if leverage > _decimal("max_leverage", rules["max_leverage"]):
        blockers.append("Requested leverage exceeds the contract maximum.")
    if stop <= 0 or (side == "long" and stop >= entry) or (side == "short" and stop <= entry):
        blockers.append("Tick-rounded stop is invalid relative to entry.")

    targets = []
    for target in plan.get("targets") or []:
        raw = _decimal("target price", target.get("price"), positive=True)
        rounded = _floor_step(raw, tick) if side == "long" else _ceil_step(raw, tick)
        if (side == "long" and rounded <= entry) or (side == "short" and rounded >= entry):
            blockers.append("A tick-rounded target is invalid relative to entry.")
        targets.append({**target, "price": _as_float(rounded)})

    selected_tier = None
    for tier in rules["maintenance_margin_tiers"]:
        if notional <= _decimal("tier notional_cap", tier["notional_cap"], positive=True):
            selected_tier = tier
            break
    if selected_tier is None:
        selected_tier = rules["maintenance_margin_tiers"][-1]
        if notional > _decimal("tier notional_cap", selected_tier["notional_cap"], positive=True):
            blockers.append("Notional exceeds the highest published maintenance-margin tier.")
    else:
        if leverage > _decimal("tier max_leverage", selected_tier["max_leverage"], positive=True):
            blockers.append("Requested leverage exceeds the selected notional tier maximum.")

    status = "NO_TRADE" if blockers else "READY_FOR_REVIEW"
    return {
        "status": status,
        "actionable": False,
        "exchange": str(rules.get("exchange", "unspecified")),
        "symbol": str(rules["symbol"]),
        "contract_type": "linear",
        "quote_asset": str(rules["quote_asset"]),
        "side": side,
        "entry_reference": _as_float(entry),
        "quantity_base_raw": _as_float(qty_raw),
        "quantity_base": _as_float(qty),
        "quantity_step": _as_float(step),
        "notional": _as_float(notional),
        "stop_loss": _as_float(stop),
        "targets": targets,
        "leverage": _as_float(leverage),
        "maintenance_margin_tier": dict(selected_tier),
        "maintenance_margin_rate": float(selected_tier["maintenance_margin_rate"]),
        "blockers": blockers,
        "warnings": [
            "Rules must come from fresh exchange contract metadata; stale rules are not authorization.",
            "Funding, mark-price liquidation, position mode, reduce-only behavior and live order-book slippage require separate checks.",
            "This validator does not submit, amend or cancel orders."
        ],
    }


__all__ = ["validate_futures_rules", "apply_futures_filters"]
