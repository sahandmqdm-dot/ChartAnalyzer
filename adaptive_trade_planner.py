"""Adaptive, fail-closed trade planning for linear Spot/Futures instruments.

Independent of the signal generator. Sizes positions from account risk, derives
ATR/structure-aware invalidation and R targets, and refuses authorization
without explicit OOS approval. This is not an exchange execution oracle.
"""
from __future__ import annotations
import math
from typing import Any, Dict, Iterable, Optional

ENGINE_VERSION = "AdaptiveTradePlanningEngine 1.0.0"

def _num(name: str, value: Any, *, minimum: Optional[float] = None, strict: bool = False) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    if minimum is not None and (result <= minimum if strict else result < minimum):
        raise ValueError(f"{name} must be {'>' if strict else '>='} {minimum}")
    return result

def build_trade_plan(*, side: str, entry_price: float, atr: float, account_equity: float,
    market_type: str = "futures", risk_fraction: float = 0.005, max_leverage: float = 5.0,
    stop_atr_multiple: float = 1.5, target_r_multiples: Iterable[float] = (2.0, 3.0),
    margin_budget_fraction: float = 0.25, maintenance_margin_rate: float = 0.005,
    liquidation_fee_rate: float = 0.005, liquidation_buffer_multiple: float = 2.0,
    fee_roundtrip_rate: float = 0.0, slippage_roundtrip_rate: float = 0.0,
    swing_low: Optional[float] = None, swing_high: Optional[float] = None,
    structure_buffer_atr: float = 0.10, oos_authorized: bool = False,
    data_integrity_valid: bool = False, signal_valid: bool = False,
    deployment_gate: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return a risk-sized plan; all money values use quote currency.

    risk_fraction=0.005 means 0.5% of account equity, not a 0.5% stop.
    Structure levels widen the ATR stop when they lie beyond it. The caller
    must explicitly provide current signal, data-integrity and OOS authorization.
    """
    direction, market = str(side).strip().lower(), str(market_type).strip().lower()
    if direction not in {"long", "short"}: raise ValueError("side must be 'long' or 'short'")
    if market not in {"spot", "futures"}: raise ValueError("market_type must be 'spot' or 'futures'")
    entry = _num("entry_price", entry_price, minimum=0, strict=True)
    atr_v = _num("atr", atr, minimum=0, strict=True)
    equity = _num("account_equity", account_equity, minimum=0, strict=True)
    risk = _num("risk_fraction", risk_fraction, minimum=0, strict=True)
    if risk > 0.02: raise ValueError("risk_fraction above 2% is blocked by the safety ceiling")
    max_lev = _num("max_leverage", max_leverage, minimum=1)
    if max_lev > 5: raise ValueError("max_leverage cannot exceed the engine safety ceiling of 5x")
    stop_mult = _num("stop_atr_multiple", stop_atr_multiple, minimum=0, strict=True)
    margin_budget = _num("margin_budget_fraction", margin_budget_fraction, minimum=0, strict=True)
    if margin_budget > 1: raise ValueError("margin_budget_fraction must be <= 1")
    mmr = _num("maintenance_margin_rate", maintenance_margin_rate, minimum=0)
    liq_fee = _num("liquidation_fee_rate", liquidation_fee_rate, minimum=0)
    if mmr + liq_fee >= 0.25: raise ValueError("maintenance and liquidation fee reserves are too high")
    buffer_mult = _num("liquidation_buffer_multiple", liquidation_buffer_multiple, minimum=1)
    fee = _num("fee_roundtrip_rate", fee_roundtrip_rate, minimum=0)
    slip = _num("slippage_roundtrip_rate", slippage_roundtrip_rate, minimum=0)
    if fee + slip >= 0.25: raise ValueError("combined round-trip cost rate must be < 25%")
    structure_buffer = _num("structure_buffer_atr", structure_buffer_atr, minimum=0)
    low = _num("swing_low", swing_low, minimum=0, strict=True) if swing_low is not None else None
    high = _num("swing_high", swing_high, minimum=0, strict=True) if swing_high is not None else None

    distance = atr_v * stop_mult
    if direction == "long":
        stop = entry - distance
        if low is not None: stop = min(stop, low - atr_v * structure_buffer)
        if stop <= 0 or stop >= entry: raise ValueError("long stop must be positive and below entry")
        distance = entry - stop
    else:
        stop = entry + distance
        if high is not None: stop = max(stop, high + atr_v * structure_buffer)
        distance = stop - entry

    raw_targets = [_num("target R multiple", r, minimum=0, strict=True) for r in target_r_multiples]
    if not raw_targets or len(set(raw_targets)) != len(raw_targets):
        raise ValueError("target R multiples must be non-empty and unique")
    stop_pct = distance / entry
    lev_headroom = 1.0 / (buffer_mult * stop_pct + mmr + liq_fee)
    leverage = min(max_lev, max(1.0, lev_headroom)) if market == "futures" else 1.0

    risk_budget = equity * risk
    cost_per_unit = entry * (fee + slip)
    qty_risk = risk_budget / (distance + cost_per_unit)
    qty_margin = equity * margin_budget * leverage / entry
    qty = min(qty_risk, qty_margin)
    if not math.isfinite(qty) or qty <= 0: raise ValueError("calculated position quantity is invalid")
    notional, initial_margin = qty * entry, qty * entry / leverage
    stop_loss_amount = qty * distance
    modeled_cost = qty * cost_per_unit
    modeled_loss = stop_loss_amount + modeled_cost
    targets = []
    for i, r in enumerate(raw_targets, 1):
        price = entry + distance*r if direction == "long" else entry-distance*r
        if price <= 0: raise ValueError("short target price must remain positive")
        gross = qty * distance * r
        targets.append({"label": f"TP{i}", "price": price, "r_multiple": r,
            "gross_pnl": gross, "estimated_net_pnl_before_funding": gross-modeled_cost})

    blockers = []
    if not signal_valid: blockers.append("No valid current signal was explicitly supplied.")
    if not data_integrity_valid: blockers.append("OHLCV/data-integrity validation has not passed.")
    if not oos_authorized: blockers.append("Matching fresh out-of-sample deployment authorization is absent.")
    if deployment_gate is not None and deployment_gate.get("eligible") is not True:
        blockers.append("The supplied deployment gate is not eligible.")
    if any(t["estimated_net_pnl_before_funding"] <= 0 for t in targets):
        blockers.append("At least one target has non-positive modeled net reward after costs.")

    return {
        "engine": ENGINE_VERSION, "status": "READY_FOR_REVIEW" if not blockers else "NO_TRADE",
        "actionable": False, "side": direction, "market_type": market,
        "entry_reference": entry, "stop_loss": stop, "stop_distance": distance,
        "stop_distance_pct": stop_pct*100, "targets": targets,
        "account_equity": equity, "risk_fraction": risk, "risk_budget": risk_budget,
        "quantity_base": qty, "notional": notional, "leverage": leverage,
        "initial_margin_estimate": initial_margin, "margin_budget": equity*margin_budget,
        "position_sizing_limited_by": "risk_budget" if qty_risk <= qty_margin else "margin_budget",
        "modeled_stop_loss": stop_loss_amount, "modeled_roundtrip_cost": modeled_cost,
        "modeled_total_loss_at_stop": modeled_loss,
        "account_risk_at_stop_pct": modeled_loss/equity*100,
        "liquidation_buffer_multiple": buffer_mult, "leverage_headroom_estimate": lev_headroom,
        "assumptions": [
            "Linear quote-settled contract and base-asset quantity; inverse contracts unsupported.",
            "Isolated-margin approximation only; exchange tiers, mark-price liquidation, funding and partial fills omitted.",
            "ATR and swing levels must be computed causally from completed candles.",
            "Entry is a reference price; fees and slippage are explicit estimates.",
            "READY_FOR_REVIEW is not an order; verify exchange rules and live prices."
        ],
        "blockers": blockers,
        "risk_disclaimer": "Planning estimate only; not a guarantee or financial advice."
    }

__all__ = ["build_trade_plan"]
