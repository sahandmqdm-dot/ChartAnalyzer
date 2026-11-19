"""Fail-closed venue-filter validation for US cash-equity trade plans.

Rules must be normalized from a trusted venue/broker metadata source. No order
submission, borrow locate, NBBO or live halt feed is implemented here.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_UP
from typing import Any


def _d(name: str, value: Any, *, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite decimal") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'finite'}")
    return result


def _floor(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _ceil(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_UP) * step


def validate_equity_rules(rules: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(rules, dict):
        raise ValueError("equity rules must be a mapping")
    required = ("symbol", "venue", "status", "tick_size", "lot_size",
                "min_qty", "max_qty", "min_notional", "shortable")
    missing = [key for key in required if key not in rules or rules[key] is None]
    if missing:
        raise ValueError("missing equity rules: " + ", ".join(missing))
    if str(rules["venue"]).upper() not in {"NASDAQ", "NYSE"}:
        raise ValueError("unsupported US equity venue")
    if str(rules["status"]).upper() != "TRADING":
        raise ValueError("equity is not in TRADING status")
    if not str(rules["symbol"]).strip():
        raise ValueError("symbol is required")
    for key in ("tick_size", "lot_size", "min_qty", "max_qty", "min_notional"):
        _d(key, rules[key], positive=True)
    if _d("min_qty", rules["min_qty"]) > _d("max_qty", rules["max_qty"]):
        raise ValueError("min_qty cannot exceed max_qty")
    if not isinstance(rules["shortable"], bool):
        raise ValueError("shortable must be boolean")
    if "ssr_active" in rules and not isinstance(rules["ssr_active"], bool):
        raise ValueError("ssr_active must be boolean")
    if "short_sale_price_test_compliant" in rules and not isinstance(rules["short_sale_price_test_compliant"], bool):
        raise ValueError("short_sale_price_test_compliant must be boolean")
    if "borrow_available" in rules and not isinstance(rules["borrow_available"], bool):
        raise ValueError("borrow_available must be boolean")
    return rules


def apply_equity_filters(plan: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    """Apply share, tick, notional, trading-status and short-sale constraints."""
    validate_equity_rules(rules)
    if not isinstance(plan, dict):
        raise ValueError("trade plan must be a mapping")
    if str(plan.get("market_type", "")).lower() != "equity":
        raise ValueError("equity filters cannot be applied to a non-equity plan")
    if plan.get("status") != "READY_FOR_REVIEW":
        return {"status": "NO_TRADE", "actionable": False, "blockers": ["Only READY_FOR_REVIEW plans can be checked."]}
    side = str(plan.get("side", "")).lower()
    if side not in {"long", "short"}:
        raise ValueError("side must be long or short")
    entry = _d("entry_reference", plan.get("entry_reference"), positive=True)
    raw_qty = _d("quantity", plan.get("quantity"), positive=True)
    tick, lot = _d("tick_size", rules["tick_size"], positive=True), _d("lot_size", rules["lot_size"], positive=True)
    qty = _floor(raw_qty, lot)
    stop_raw = _d("stop_loss", plan.get("stop_loss"), positive=True)
    stop = _ceil(stop_raw, tick) if side == "long" else _floor(stop_raw, tick)
    blockers: list[str] = []
    if qty < _d("min_qty", rules["min_qty"]):
        blockers.append("Share quantity is below venue minimum after lot rounding.")
    if qty > _d("max_qty", rules["max_qty"]):
        blockers.append("Share quantity exceeds venue maximum.")
    notional = qty * entry
    if notional < _d("min_notional", rules["min_notional"]):
        blockers.append("Order notional is below venue minimum.")
    if side == "short":
        if not rules["shortable"]:
            blockers.append("Instrument is not marked shortable.")
        if rules.get("borrow_available") is not True:
            blockers.append("Confirmed borrow availability is required for short sales.")
        if rules.get("ssr_active", False) and rules.get("short_sale_price_test_compliant") is not True:
            blockers.append("Short-sale price-test compliance is not confirmed while SSR is active.")
    if stop <= 0 or (side == "long" and stop >= entry) or (side == "short" and stop <= entry):
        blockers.append("Tick-rounded stop is invalid relative to entry.")
    targets = []
    for target in plan.get("targets") or []:
        if not isinstance(target, dict):
            raise ValueError("each target must be a mapping")
        raw = _d("target price", target.get("price"), positive=True)
        rounded = _floor(raw, tick) if side == "long" else _ceil(raw, tick)
        if (side == "long" and rounded <= entry) or (side == "short" and rounded >= entry):
            blockers.append("A tick-rounded target is invalid relative to entry.")
        targets.append({**target, "price": float(rounded)})
    return {
        "status": "NO_TRADE" if blockers else "READY_FOR_REVIEW",
        "actionable": False,
        "venue": str(rules["venue"]).upper(),
        "symbol": str(rules["symbol"]).upper(),
        "side": side,
        "entry_reference": float(entry),
        "quantity_raw": float(raw_qty),
        "quantity": float(qty),
        "lot_size": float(lot),
        "notional": float(notional),
        "stop_loss": float(stop),
        "targets": targets,
        "blockers": blockers,
        "warnings": [
            "Rules must be fresh and sourced from the actual listing/broker metadata.",
            "Live halts, NBBO, borrow locate and venue-specific order types require separate controls.",
            "This validator does not submit, amend or cancel orders.",
        ],
    }


__all__ = ["validate_equity_rules", "apply_equity_filters"]
