from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RiskTier(BaseModel):
    """One linear-contract notional bracket; cap is exclusive."""

    model_config = ConfigDict(frozen=True)

    tier: int = Field(ge=1)
    notional_floor: Decimal = Field(ge=0)
    notional_cap: Decimal | None = Field(default=None, gt=0)
    maintenance_margin_rate: Decimal = Field(gt=0, lt=1)
    maintenance_margin_deduction: Decimal = Field(default=Decimal(0), ge=0)
    max_leverage: Decimal | None = Field(default=None, gt=0)


class IsolatedRiskEstimateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=2, max_length=30)
    side: Literal["long", "short"]
    entry_price: Decimal = Field(gt=0)
    quantity: Decimal = Field(gt=0)
    leverage: Decimal = Field(gt=0)
    extra_margin: Decimal = Field(default=Decimal(0), ge=0)
    estimated_close_fee_rate: Decimal = Field(default=Decimal("0.0005"), ge=0, lt=1)
    risk_tiers: list[RiskTier] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_tiers(self):
        tiers = self.risk_tiers
        if tiers[0].notional_floor != 0:
            raise ValueError("the first risk tier must start at zero")
        for index, tier in enumerate(tiers):
            if index and tier.notional_floor != tiers[index - 1].notional_cap:
                raise ValueError("risk tiers must be ordered and contiguous")
            if tier.notional_cap is not None and tier.notional_cap <= tier.notional_floor:
                raise ValueError("risk tier cap must exceed its floor")
            if index < len(tiers) - 1 and tier.notional_cap is None:
                raise ValueError("only the final risk tier may have no upper cap")
        if tiers[-1].notional_cap is not None:
            raise ValueError("the final risk tier must have no upper cap")
        return self


class IsolatedRiskEstimate(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    side: Literal["long", "short"]
    margin_mode: Literal["isolated"] = "isolated"
    entry_price: Decimal
    quantity: Decimal
    leverage: Decimal
    entry_notional: Decimal
    initial_margin_estimate: Decimal
    extra_margin: Decimal
    liquidation_price_estimate: Decimal
    liquidation_tier: int
    maintenance_margin_rate: Decimal
    maintenance_margin_estimate: Decimal
    estimated_close_fee: Decimal
    distance_to_liquidation_percent: Decimal
    status: Literal["estimate_only"] = "estimate_only"
    assumptions: list[str]


def _tier_for_notional(tiers: list[RiskTier], notional: Decimal) -> RiskTier | None:
    for tier in reversed(tiers):
        if notional >= tier.notional_floor and (
            tier.notional_cap is None or notional < tier.notional_cap
        ):
            return tier
    return None


def estimate_isolated_liquidation(
    request: IsolatedRiskEstimateRequest,
) -> IsolatedRiskEstimate:
    """Solve isolated linear USDT-margined liquidation using exchange tier deductions.

    Cross/portfolio margin, funding cashflows, open orders, insurance/ADL,
    fee-token discounts and exchange-specific liquidation fees are out of scope.
    """
    symbol = request.symbol.strip().upper()
    entry_notional = request.entry_price * request.quantity
    entry_tier = _tier_for_notional(request.risk_tiers, entry_notional)
    if entry_tier is None:
        raise ValueError("entry notional is outside the supplied risk tiers")
    if entry_tier.max_leverage is not None and request.leverage > entry_tier.max_leverage:
        raise ValueError("requested leverage exceeds the entry tier maximum")

    initial_margin = entry_notional / request.leverage + request.extra_margin
    fee_rate = request.estimated_close_fee_rate
    candidate: tuple[Decimal, RiskTier] | None = None

    for tier in request.risk_tiers:
        if request.side == "long":
            denominator = request.quantity * (
                Decimal(1) - tier.maintenance_margin_rate - fee_rate
            )
            numerator = entry_notional - initial_margin - tier.maintenance_margin_deduction
        else:
            denominator = request.quantity * (
                Decimal(1) + tier.maintenance_margin_rate + fee_rate
            )
            numerator = (
                initial_margin + entry_notional + tier.maintenance_margin_deduction
            )
        if denominator <= 0:
            continue
        price = numerator / denominator
        if price <= 0:
            continue
        liquidation_notional = price * request.quantity
        if _tier_for_notional(request.risk_tiers, liquidation_notional) == tier:
            candidate = (price, tier)
            break

    if candidate is None:
        raise ValueError("could not resolve a positive liquidation price within supplied tiers")

    liquidation_price, liquidation_tier = candidate
    liquidation_notional = liquidation_price * request.quantity
    maintenance_margin = (
        liquidation_notional * liquidation_tier.maintenance_margin_rate
        - liquidation_tier.maintenance_margin_deduction
    )
    close_fee = liquidation_notional * fee_rate
    distance = (
        (request.entry_price - liquidation_price) / request.entry_price * 100
        if request.side == "long"
        else (liquidation_price - request.entry_price) / request.entry_price * 100
    )
    if distance <= 0:
        raise ValueError("the supplied margin inputs produce a liquidation price on the wrong side")

    return IsolatedRiskEstimate(
        symbol=symbol,
        side=request.side,
        entry_price=request.entry_price,
        quantity=request.quantity,
        leverage=request.leverage,
        entry_notional=entry_notional,
        initial_margin_estimate=initial_margin,
        extra_margin=request.extra_margin,
        liquidation_price_estimate=liquidation_price,
        liquidation_tier=liquidation_tier.tier,
        maintenance_margin_rate=liquidation_tier.maintenance_margin_rate,
        maintenance_margin_estimate=maintenance_margin,
        estimated_close_fee=close_fee,
        distance_to_liquidation_percent=distance,
        assumptions=[
            "Isolated margin only; cross and portfolio margin are not modeled.",
            "Linear quote-settled contract with quantity expressed in base-asset units.",
            "Uses the supplied exchange risk tiers and their cumulative maintenance deductions.",
            "Close fee is a user-supplied estimate; funding payments and other account costs are excluded.",
            "The result is an estimate, not the exchange liquidation engine's guaranteed trigger price.",
        ],
    )
