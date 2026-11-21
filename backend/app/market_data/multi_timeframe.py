from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.market_data.market_structure import MarketStructureAnalysis

Bias = Literal["bullish", "bearish", "neutral"]


class TimeframeConfluence(BaseModel):
    model_config = ConfigDict(frozen=True)

    period: str
    bias: Bias
    trend: str
    latest_close: str | None
    closed_candles: int
    latest_event: str | None
    active_bullish_fvgs: int
    active_bearish_fvgs: int
    active_bullish_order_blocks: int
    active_bearish_order_blocks: int
    evidence: list[str]
    caveats: list[str]


class MultiTimeframeConfluence(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    provider: str
    alignment: str
    dominant_bias: Bias
    directional_timeframes: int
    bullish_timeframes: int
    bearish_timeframes: int
    neutral_timeframes: int
    timeframes: list[TimeframeConfluence]
    observed_at: datetime
    caveats: list[str]


def _summarize(analysis: MarketStructureAnalysis) -> TimeframeConfluence:
    directional_events = [
        event for event in analysis.events
        if event.code in {"bos", "choch"} and event.direction in {"bullish", "bearish"}
    ]
    latest_event = directional_events[-1] if directional_events else None
    if analysis.trend in {"bullish", "bearish"}:
        bias: Bias = analysis.trend  # type: ignore[assignment]
    elif latest_event is not None:
        bias = latest_event.direction  # type: ignore[assignment]
    else:
        bias = "neutral"

    evidence: list[str] = []
    if analysis.trend in {"bullish", "bearish"}:
        evidence.append(f"ساختار HH/HL یا LH/LL به نفع {analysis.trend} است.")
    elif analysis.trend == "range":
        evidence.append("ساختار Swingها در وضعیت خنثی/رنج است.")
    if latest_event is not None:
        evidence.append(
            f"آخرین رویداد جهت‌دار {latest_event.code.upper()} در جهت {latest_event.direction} است."
        )
    sweeps = [event for event in analysis.events if event.code == "liquidity_sweep"]
    for event in sweeps:
        evidence.append(f"Liquidity sweep با جهت {event.direction} در سطح {event.level} ثبت شده است.")
    active_gaps = [item for item in analysis.fair_value_gaps if item.status in {"active", "partially_filled"}]
    active_blocks = [item for item in analysis.order_blocks if item.status == "active"]
    evidence.append(
        f"محدوده‌های فعال: {sum(x.direction == 'bullish' for x in active_gaps)} FVG صعودی، "
        f"{sum(x.direction == 'bearish' for x in active_gaps)} FVG نزولی، "
        f"{sum(x.direction == 'bullish' for x in active_blocks)} OB صعودی و "
        f"{sum(x.direction == 'bearish' for x in active_blocks)} OB نزولی."
    )
    return TimeframeConfluence(
        period=analysis.period,
        bias=bias,
        trend=analysis.trend,
        latest_close=str(analysis.latest_close) if analysis.latest_close is not None else None,
        closed_candles=analysis.closed_candles,
        latest_event=latest_event.code if latest_event else None,
        active_bullish_fvgs=sum(x.direction == "bullish" for x in active_gaps),
        active_bearish_fvgs=sum(x.direction == "bearish" for x in active_gaps),
        active_bullish_order_blocks=sum(x.direction == "bullish" for x in active_blocks),
        active_bearish_order_blocks=sum(x.direction == "bearish" for x in active_blocks),
        evidence=evidence,
        caveats=analysis.caveats,
    )


def build_multi_timeframe_confluence(
    *,
    symbol: str,
    provider: str,
    analyses: list[MarketStructureAnalysis],
    observed_at: datetime,
) -> MultiTimeframeConfluence:
    """Summarize same-venue, closed-candle structure without opaque scoring."""
    summaries = [_summarize(item) for item in analyses]
    bullish = sum(item.bias == "bullish" for item in summaries)
    bearish = sum(item.bias == "bearish" for item in summaries)
    neutral = sum(item.bias == "neutral" for item in summaries)
    directional = bullish + bearish

    if directional < 2:
        alignment = "insufficient_directional_data"
        dominant: Bias = "neutral"
    elif bullish and bearish:
        alignment = "conflicted"
        dominant = "neutral"
    elif bullish == directional:
        dominant = "bullish"
        alignment = "aligned" if neutral == 0 else "partial_alignment"
    else:
        dominant = "bearish"
        alignment = "aligned" if neutral == 0 else "partial_alignment"

    return MultiTimeframeConfluence(
        symbol=symbol,
        provider=provider,
        alignment=alignment,
        dominant_bias=dominant,
        directional_timeframes=directional,
        bullish_timeframes=bullish,
        bearish_timeframes=bearish,
        neutral_timeframes=neutral,
        timeframes=summaries,
        observed_at=observed_at,
        caveats=[
            "همه تایم‌فریم‌ها از کندل‌های بسته‌شده Mark Price و از یک ارائه‌دهنده مشتقات تحلیل می‌شوند.",
            "همگرایی بر اساس رأی کیفی روند ساختاری و آخرین BOS/CHoCH محاسبه می‌شود؛ امتیاز احتمالاتی یا پیش‌بینی قیمت نیست.",
            "تعداد FVG و Order Block فقط شواهد زمینه‌ای است و به‌تنهایی جهت تایم‌فریم را تغییر نمی‌دهد.",
            "نتایج به قواعد پیوت و تعاریف ابتکاری BOS/CHoCH وابسته‌اند؛ این خروجی توصیه معامله یا تضمین نتیجه نیست.",
        ],
    )
