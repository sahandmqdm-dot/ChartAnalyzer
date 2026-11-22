from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.market_data.futures_analysis import FuturesAnalysis


class MarketPressureAssessment(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    bias: str
    pressure_score: int
    confidence: str
    price_change_pct: Decimal | None
    open_interest_change_pct: Decimal | None
    funding_rate: Decimal | None
    long_liquidation_notional: Decimal
    short_liquidation_notional: Decimal
    liquidation_imbalance_pct: Decimal | None
    signals: list[str]
    caveats: list[str]


def assess_market_pressure(
    analysis: FuturesAnalysis,
    liquidation_events: list,
) -> MarketPressureAssessment:
    """Combine price/OI/funding context with observed liquidation flow.

    Score is an explainable heuristic, not a probability or trading instruction.
    """
    long_notional = Decimal(0)
    short_notional = Decimal(0)
    for event in liquidation_events:
        notional = Decimal(str(event.notional))
        if not notional.is_finite() or notional < 0:
            continue
        if event.liquidation_side == "long":
            long_notional += notional
        elif event.liquidation_side == "short":
            short_notional += notional

    total_liquidations = long_notional + short_notional
    imbalance = (
        (long_notional - short_notional) / total_liquidations * Decimal(100)
        if total_liquidations > 0
        else None
    )

    score = 0
    signals: list[str] = []
    price = analysis.price_change_pct
    oi = analysis.open_interest_change_pct
    funding = analysis.latest_funding_rate

    if price is not None and oi is not None:
        if price > 0 and oi > 0:
            score += 1
            signals.append("price_and_open_interest_rising")
        elif price < 0 and oi > 0:
            score -= 1
            signals.append("price_falling_with_new_open_interest")
        elif price > 0 and oi < 0:
            signals.append("price_rising_with_deleveraging")
        elif price < 0 and oi < 0:
            signals.append("price_falling_with_deleveraging")

    if funding is not None:
        if funding >= Decimal("0.001"):
            score -= 1
            signals.append("longs_pay_elevated_funding")
        elif funding <= Decimal("-0.001"):
            score += 1
            signals.append("shorts_pay_elevated_funding")

    if imbalance is not None:
        if imbalance >= Decimal(25):
            score -= 1
            signals.append("long_liquidations_dominate_observed_flow")
        elif imbalance <= Decimal(-25):
            score += 1
            signals.append("short_liquidations_dominate_observed_flow")

    if score >= 2:
        bias = "bullish_pressure"
    elif score <= -2:
        bias = "bearish_pressure"
    elif score > 0:
        bias = "mild_bullish_pressure"
    elif score < 0:
        bias = "mild_bearish_pressure"
    else:
        bias = "balanced_or_insufficient"

    if price is None or oi is None or funding is None or imbalance is None or len(liquidation_events) < 3:
        confidence = "low"
    else:
        confidence = "medium"

    return MarketPressureAssessment(
        symbol=analysis.symbol,
        bias=bias,
        pressure_score=score,
        confidence=confidence,
        price_change_pct=price,
        open_interest_change_pct=oi,
        funding_rate=funding,
        long_liquidation_notional=long_notional,
        short_liquidation_notional=short_notional,
        liquidation_imbalance_pct=imbalance,
        signals=signals,
        caveats=[
            "Pressure score is a transparent heuristic, not a calibrated probability or standalone entry signal.",
            "Liquidation values include only observed events returned by the configured provider and query limit; missing events can distort the imbalance.",
            "Open Interest is aggregate and does not identify whether newly opened contracts are net long or net short.",
            "Funding and liquidation thresholds are fixed exploratory rules and require symbol-specific backtesting before operational use.",
        ],
    )
