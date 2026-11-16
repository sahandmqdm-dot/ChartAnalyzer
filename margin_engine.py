"""ChartAnalyzer V6.3 isolated-margin estimator for linear USDT/quote-settled futures.

This is an exchange-neutral approximation, not an exchange liquidation oracle.
It assumes an isolated position, linear PnL, a single maintenance-margin rate,
no cross collateral, no tier brackets, and no historical fee/funding schedule.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional


def _finite_number(name: str, value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(out):
        raise ValueError(f"{name} must be a finite number")
    return out


def calculate_isolated_margin(
    *,
    market_type: str = "futures",
    side: str = "long",
    entry_price: float,
    quantity: float,
    leverage: float = 1.0,
    maintenance_margin_rate: float = 0.005,
    additional_margin: float = 0.0,
    liquidation_fee_rate: float = 0.0,
    mark_price: Optional[float] = None,
    max_leverage: float = 125.0,
    warning_distance_pct: float = 5.0,
    critical_distance_pct: float = 2.0,
) -> Dict[str, Any]:
    """Estimate isolated margin and liquidation price for a linear futures position.

    `quantity` is base-asset units (e.g. BTC quantity), rates are decimal fractions,
    and all monetary fields are in the quote currency. Optional `mark_price`
    activates current unrealized PnL / margin-health diagnostics.
    """
    market = str(market_type).strip().lower()
    if market != "futures":
        raise ValueError("margin calculator supports futures only; Spot has no liquidation price")

    direction = str(side).strip().lower()
    if direction not in {"long", "short"}:
        raise ValueError("side must be 'long' or 'short'")
    sign = 1.0 if direction == "long" else -1.0

    entry = _finite_number("entry_price", entry_price)
    qty = _finite_number("quantity", quantity)
    lev = _finite_number("leverage", leverage)
    mmr = _finite_number("maintenance_margin_rate", maintenance_margin_rate)
    extra = _finite_number("additional_margin", additional_margin)
    liq_fee = _finite_number("liquidation_fee_rate", liquidation_fee_rate)
    max_lev = _finite_number("max_leverage", max_leverage)
    warn = _finite_number("warning_distance_pct", warning_distance_pct)
    critical = _finite_number("critical_distance_pct", critical_distance_pct)

    if entry <= 0 or qty <= 0:
        raise ValueError("entry_price and quantity must be > 0")
    if lev < 1 or lev > max_lev or max_lev < 1:
        raise ValueError(f"leverage must be between 1 and configured max_leverage ({max_lev:g}x)")
    if not 0 <= mmr < 0.5:
        raise ValueError("maintenance_margin_rate must be in [0, 0.5)")
    if not 0 <= liq_fee < 0.5 or mmr + liq_fee >= 0.5:
        raise ValueError("liquidation_fee_rate must be >= 0 and combined margin rates < 0.5")
    if extra < 0:
        raise ValueError("additional_margin must be >= 0")
    if warn <= 0 or critical < 0 or critical >= warn:
        raise ValueError("risk thresholds must satisfy 0 <= critical < warning")

    notional = entry * qty
    initial_margin = notional / lev
    collateral = initial_margin + extra
    effective_rate = mmr + liq_fee

    # Isolated-wallet equity at liquidation equals maintenance requirement plus
    # the configured liquidation-fee reserve. Cross margin/tier brackets omitted.
    collateral_per_unit = collateral / qty
    if direction == "long":
        raw_liq = (entry - collateral_per_unit) / (1.0 - effective_rate)
        liq_price = max(0.0, raw_liq)
    else:
        liq_price = (entry + collateral_per_unit) / (1.0 + effective_rate)

    out: Dict[str, Any] = {
        "engine": "linear_isolated_margin_estimator_v1",
        "market_type": "futures",
        "margin_mode": "isolated",
        "contract_type": "linear",
        "side": direction,
        "entry_price": entry,
        "quantity": qty,
        "leverage": lev,
        "notional": notional,
        "initial_margin": initial_margin,
        "additional_margin": extra,
        "total_isolated_collateral": collateral,
        "maintenance_margin_rate": mmr,
        "liquidation_fee_rate": liq_fee,
        "effective_liquidation_rate": effective_rate,
        "maintenance_margin_at_entry": notional * mmr,
        "estimated_liquidation_price": liq_price,
        "liquidation_distance_from_entry_pct": abs(entry - liq_price) / entry * 100.0,
        "max_leverage": max_lev,
        "assumptions": [
            "Linear quote-settled contract; quantity is in base-asset units.",
            "Isolated margin only; no cross collateral or tiered maintenance-margin brackets.",
            "Liquidation fee is added to the maintenance-rate reserve for a conservative estimate.",
            "Exchange-specific mark-price rules, risk tiers, order-book impact and fee schedules are not modelled.",
        ],
        "warning": "Estimated liquidation price only. Verify maintenance-margin tiers and liquidation rules with the target exchange before trading.",
    }

    if mark_price is not None:
        mark = _finite_number("mark_price", mark_price)
        if mark <= 0:
            raise ValueError("mark_price must be > 0")
        unrealized = qty * (mark - entry) * sign
        equity = collateral + unrealized
        current_notional = mark * qty
        maintenance_now = current_notional * mmr
        # Keep the same conservative liquidation-fee reserve used by the
        # liquidation-price equation when evaluating current margin health.
        reserve_now = current_notional * effective_rate
        margin_ratio = (reserve_now / equity * 100.0) if equity > 0 else math.inf
        if direction == "long":
            distance = (mark - liq_price) / mark * 100.0
        else:
            distance = (liq_price - mark) / mark * 100.0
        deviation_pct = abs(mark - entry) / entry * 100.0
        scale_warning = deviation_pct > 30.0
        # A unit/market mismatch invalidates any directional health verdict.
        # Keep raw estimates for debugging, but never present them as verified.
        if scale_warning:
            health = "unverified_inputs"
        elif distance <= 0 or equity <= reserve_now:
            health = "liquidation_threshold_reached"
        elif distance <= critical:
            health = "critical"
        elif distance <= warn:
            health = "warning"
        else:
            health = "healthy"
        out.update({
            "mark_price": mark,
            "mark_entry_deviation_pct": deviation_pct,
            "price_scale_warning": ("Mark price differs from entry price by more than 30%; verify that both prices use the same market and quote-currency units." if scale_warning else None),
            "margin_health_verified": not scale_warning,
            "unrealized_pnl": unrealized if not scale_warning else None,
            "current_equity_estimate": equity if not scale_warning else None,
            "maintenance_margin_at_mark": maintenance_now if not scale_warning else None,
            "liquidation_reserve_at_mark": reserve_now if not scale_warning else None,
            "equity_after_liquidation_reserve": (equity - reserve_now) if not scale_warning else None,
            "margin_ratio_pct": (margin_ratio if math.isfinite(margin_ratio) else None) if not scale_warning else None,
            "distance_to_liquidation_pct": distance if not scale_warning else None,
            "margin_health": health,
            "risk_metrics_status": "verified" if not scale_warning else "suppressed_due_to_price_scale_mismatch",
        })
    return out


__all__ = ["calculate_isolated_margin"]
