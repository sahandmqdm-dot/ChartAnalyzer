"""Tier-aware liquidation and margin diagnostics for linear isolated futures.

Consumes normalized exchange risk brackets. This module is an estimator, not an
exchange liquidation oracle. It never submits orders and fails closed when
bracket metadata is incomplete or the liquidation solution is ambiguous.
"""
from __future__ import annotations

import math
from typing import Any


def _number(name: str, value: Any, *, positive: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'finite'}")
    return result


def validate_margin_tiers(tiers: list[dict[str, Any]]) -> list[dict[str, float]]:
    """Validate inclusive upper-notional brackets with cumulative maintenance deductions.

    Each tier requires: notional_cap, maintenance_margin_rate,
    maintenance_amount, max_leverage. maintenance_amount is the cumulative
    deduction in quote currency, not an arbitrary per-position discount.
    """
    if not isinstance(tiers, list) or not tiers:
        raise ValueError("maintenance_margin_tiers must be a non-empty list")
    normalized = []
    previous_cap = 0.0
    previous_mmr = -1.0
    for index, tier in enumerate(tiers):
        if not isinstance(tier, dict):
            raise TypeError(f"tier {index} must be a mapping")
        required = ("notional_cap", "maintenance_margin_rate", "maintenance_amount", "max_leverage")
        missing = [key for key in required if key not in tier or tier[key] is None]
        if missing:
            raise ValueError(f"tier {index} missing: {', '.join(missing)}")
        cap = _number(f"tier[{index}].notional_cap", tier["notional_cap"], positive=True)
        mmr = _number(f"tier[{index}].maintenance_margin_rate", tier["maintenance_margin_rate"])
        deduction = _number(f"tier[{index}].maintenance_amount", tier["maintenance_amount"])
        leverage = _number(f"tier[{index}].max_leverage", tier["max_leverage"], positive=True)
        if cap <= previous_cap:
            raise ValueError("tier notional caps must be strictly increasing")
        if not 0 <= mmr < 1:
            raise ValueError("maintenance margin rate must be in [0, 1)")
        if deduction < 0 or leverage < 1:
            raise ValueError("maintenance amount must be >= 0 and max leverage >= 1")
        if mmr < previous_mmr:
            raise ValueError("maintenance margin rates must be non-decreasing")
        normalized.append({
            "notional_cap": cap,
            "maintenance_margin_rate": mmr,
            "maintenance_amount": deduction,
            "max_leverage": leverage,
        })
        previous_cap, previous_mmr = cap, mmr
    return normalized


def select_margin_tier(notional: float, tiers: list[dict[str, Any]]) -> dict[str, float]:
    value = _number("notional", notional, positive=True)
    normalized = validate_margin_tiers(tiers)
    for tier in normalized:
        if value <= tier["notional_cap"]:
            return dict(tier)
    raise ValueError("position notional exceeds the highest published maintenance tier")


def _solve_liquidation_price(
    *, side: str, entry: float, quantity: float, wallet: float,
    tier: dict[str, float], liquidation_fee_rate: float,
) -> float:
    mmr = tier["maintenance_margin_rate"] + liquidation_fee_rate
    deduction = tier["maintenance_amount"]
    if mmr >= 1:
        raise ValueError("combined maintenance and liquidation fee rate must be < 1")
    if side == "long":
        denominator = quantity * (1.0 - mmr)
        return (quantity * entry - wallet - deduction) / denominator
    denominator = quantity * (1.0 + mmr)
    return (quantity * entry + wallet + deduction) / denominator


def calculate_tiered_isolated_margin(
    *, side: str, entry_price: float, quantity: float, leverage: float,
    maintenance_margin_tiers: list[dict[str, Any]], mark_price: float | None = None,
    additional_margin: float = 0.0, liquidation_fee_rate: float = 0.0,
    max_leverage: float | None = None,
) -> dict[str, Any]:
    """Estimate tiered isolated margin, liquidation price and mark-price health.

    Quantity is base-asset units; prices and margin amounts use quote currency.
    Tier selection is checked at both entry notional and solved liquidation
    notional. If the liquidation solution crosses a tier boundary, the engine
    re-solves using that bracket and verifies the result for consistency.
    """
    direction = str(side).strip().lower()
    if direction not in {"long", "short"}:
        raise ValueError("side must be long or short")
    entry = _number("entry_price", entry_price, positive=True)
    qty = _number("quantity", quantity, positive=True)
    lev = _number("leverage", leverage, positive=True)
    extra = _number("additional_margin", additional_margin)
    fee = _number("liquidation_fee_rate", liquidation_fee_rate)
    if lev < 1 or extra < 0 or not 0 <= fee < 1:
        raise ValueError("leverage must be >= 1, additional_margin >= 0 and fee rate in [0, 1)")
    tiers = validate_margin_tiers(maintenance_margin_tiers)
    entry_notional = entry * qty
    entry_tier = select_margin_tier(entry_notional, tiers)
    ceiling = _number("max_leverage", max_leverage, positive=True) if max_leverage is not None else tiers[-1]["max_leverage"]
    allowed_lev = min(ceiling, entry_tier["max_leverage"])
    if lev > allowed_lev:
        raise ValueError(f"leverage {lev:g}x exceeds selected tier maximum {allowed_lev:g}x")

    initial_margin = entry_notional / lev
    wallet = initial_margin + extra
    candidate = _solve_liquidation_price(
        side=direction, entry=entry, quantity=qty, wallet=wallet,
        tier=entry_tier, liquidation_fee_rate=fee,
    )
    if candidate <= 0 or not math.isfinite(candidate):
        raise ValueError("liquidation price is non-positive or invalid")
    liq_notional = candidate * qty
    liq_tier = select_margin_tier(liq_notional, tiers)
    if liq_tier != entry_tier:
        candidate = _solve_liquidation_price(
            side=direction, entry=entry, quantity=qty, wallet=wallet,
            tier=liq_tier, liquidation_fee_rate=fee,
        )
        if candidate <= 0 or not math.isfinite(candidate):
            raise ValueError("tier-adjusted liquidation price is invalid")
        verified_tier = select_margin_tier(candidate * qty, tiers)
        if verified_tier != liq_tier:
            raise ValueError("liquidation tier boundary is ambiguous; refusing a definitive estimate")
        liq_tier = verified_tier

    out: dict[str, Any] = {
        "engine": "tiered_isolated_margin_v1",
        "status": "ESTIMATE_ONLY",
        "actionable": False,
        "market_type": "futures",
        "contract_type": "linear",
        "margin_mode": "isolated",
        "side": direction,
        "entry_price": entry,
        "quantity": qty,
        "entry_notional": entry_notional,
        "leverage": lev,
        "initial_margin": initial_margin,
        "additional_margin": extra,
        "isolated_wallet_estimate": wallet,
        "entry_maintenance_tier": entry_tier,
        "liquidation_maintenance_tier": liq_tier,
        "maintenance_margin_at_entry": max(0.0, entry_notional * entry_tier["maintenance_margin_rate"] - entry_tier["maintenance_amount"]),
        "liquidation_fee_rate": fee,
        "estimated_liquidation_price": candidate,
        "liquidation_notional_estimate": candidate * qty,
        "liquidation_distance_from_entry_pct": abs(entry - candidate) / entry * 100.0,
        "warnings": [
            "Tier schema and cumulative maintenance deductions must be normalized from fresh exchange metadata.",
            "Exchange-specific liquidation fees, funding, insurance-fund rules, ADL and mark-price triggers may differ.",
            "This is an isolated linear-contract estimate, not a guarantee of the exchange liquidation price.",
            "No order is submitted, amended or cancelled.",
        ],
    }
    if mark_price is not None:
        mark = _number("mark_price", mark_price, positive=True)
        pnl = qty * (mark - entry) * (1 if direction == "long" else -1)
        equity = wallet + pnl
        current_notional = mark * qty
        current_tier = select_margin_tier(current_notional, tiers)
        maintenance = max(0.0, current_notional * current_tier["maintenance_margin_rate"] - current_tier["maintenance_amount"])
        reserve = current_notional * fee
        distance = ((mark - candidate) if direction == "long" else (candidate - mark)) / mark * 100.0
        if distance <= 0 or equity <= maintenance + reserve:
            health = "liquidation_threshold_reached"
        elif distance <= 2:
            health = "critical"
        elif distance <= 5:
            health = "warning"
        else:
            health = "healthy"
        out.update({
            "mark_price": mark,
            "current_notional": current_notional,
            "current_maintenance_tier": current_tier,
            "unrealized_pnl": pnl,
            "current_equity_estimate": equity,
            "maintenance_margin_at_mark": maintenance,
            "liquidation_fee_reserve_at_mark": reserve,
            "distance_to_liquidation_pct": distance,
            "margin_health": health,
        })
    return out


__all__ = ["calculate_tiered_isolated_margin", "select_margin_tier", "validate_margin_tiers"]
