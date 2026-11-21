from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.market_data.mark_price import MarkPriceCandle


class StructureSwing(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    timestamp: datetime
    price: Decimal


class StructureEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    direction: str
    timestamp: datetime
    level: Decimal
    explanation: str


class FairValueGap(BaseModel):
    model_config = ConfigDict(frozen=True)

    direction: str
    start_time: datetime
    end_time: datetime
    lower: Decimal
    upper: Decimal
    midpoint: Decimal


class MarketStructureAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    period: str
    provider: str
    trend: str
    latest_close: Decimal | None
    swings: list[StructureSwing]
    events: list[StructureEvent]
    fair_value_gaps: list[FairValueGap]
    liquidity_levels: list[Decimal]
    closed_candles: int
    pivot_window: int
    observed_at: datetime
    caveats: list[str]


def _pivots(
    candles: list[MarkPriceCandle], window: int
) -> tuple[list[StructureSwing], list[tuple[int, str, Decimal]]]:
    swings: list[StructureSwing] = []
    indexed: list[tuple[int, str, Decimal]] = []
    for index in range(window, len(candles) - window):
        candle = candles[index]
        left = candles[index - window:index]
        right = candles[index + 1:index + window + 1]
        if all(candle.high > item.high for item in left + right):
            swings.append(StructureSwing(
                kind="high", timestamp=candle.timestamp, price=candle.high
            ))
            indexed.append((index, "high", candle.high))
        if all(candle.low < item.low for item in left + right):
            swings.append(StructureSwing(
                kind="low", timestamp=candle.timestamp, price=candle.low
            ))
            indexed.append((index, "low", candle.low))
    return swings, indexed


def analyze_market_structure(
    *,
    symbol: str,
    period: str,
    provider: str,
    candles: list[MarkPriceCandle],
    observed_at: datetime,
    pivot_window: int = 3,
) -> MarketStructureAnalysis:
    """Analyze finalized mark-price candles with deterministic, confirmed rules."""
    if not 2 <= pivot_window <= 10:
        raise ValueError("pivot_window must be between 2 and 10")
    closed = sorted(
        (item for item in candles if item.is_closed),
        key=lambda item: item.timestamp,
    )
    if len(closed) < (pivot_window * 2 + 3):
        return MarketStructureAnalysis(
            symbol=symbol,
            period=period,
            provider=provider,
            trend="insufficient_data",
            latest_close=closed[-1].close if closed else None,
            swings=[],
            events=[],
            fair_value_gaps=[],
            liquidity_levels=[],
            closed_candles=len(closed),
            pivot_window=pivot_window,
            observed_at=observed_at,
            caveats=[
                "برای تأیید پیوت‌ها به حداقل دو برابر پنجره پیوت به‌علاوه سه کندل بسته‌شده نیاز است."
            ],
        )

    swings, indexed_swings = _pivots(closed, pivot_window)
    highs = [item for item in indexed_swings if item[1] == "high"]
    lows = [item for item in indexed_swings if item[1] == "low"]
    trend = "range"
    if len(highs) >= 2 and len(lows) >= 2:
        higher_highs = highs[-1][2] > highs[-2][2]
        higher_lows = lows[-1][2] > lows[-2][2]
        lower_highs = highs[-1][2] < highs[-2][2]
        lower_lows = lows[-1][2] < lows[-2][2]
        if higher_highs and higher_lows:
            trend = "bullish"
        elif lower_highs and lower_lows:
            trend = "bearish"

    last = closed[-1]
    latest_high = highs[-1] if highs else None
    latest_low = lows[-1] if lows else None
    events: list[StructureEvent] = []
    if latest_high and last.close > latest_high[2]:
        events.append(StructureEvent(
            code="bos" if trend != "bearish" else "choch",
            direction="bullish",
            timestamp=last.timestamp,
            level=latest_high[2],
            explanation=(
                "قیمت بسته‌شدن از آخرین Swing High تأییدشده عبور کرده است؛ "
                "برچسب BOS/CHoCH بر اساس روند ساختاری قبلی تعیین شده است."
            ),
        ))
    elif latest_low and last.close < latest_low[2]:
        events.append(StructureEvent(
            code="bos" if trend != "bullish" else "choch",
            direction="bearish",
            timestamp=last.timestamp,
            level=latest_low[2],
            explanation=(
                "قیمت بسته‌شدن پایین‌تر از آخرین Swing Low تأییدشده قرار گرفته است؛ "
                "برچسب BOS/CHoCH بر اساس روند ساختاری قبلی تعیین شده است."
            ),
        ))

    if latest_high and last.high > latest_high[2] and last.close <= latest_high[2]:
        events.append(StructureEvent(
            code="liquidity_sweep",
            direction="bearish",
            timestamp=last.timestamp,
            level=latest_high[2],
            explanation="سایه کندل از Swing High عبور کرده اما کندل زیر آن بسته شده است.",
        ))
    if latest_low and last.low < latest_low[2] and last.close >= latest_low[2]:
        events.append(StructureEvent(
            code="liquidity_sweep",
            direction="bullish",
            timestamp=last.timestamp,
            level=latest_low[2],
            explanation="سایه کندل از Swing Low عبور کرده اما کندل بالای آن بسته شده است.",
        ))

    gaps: list[FairValueGap] = []
    for index in range(2, len(closed)):
        first, middle, third = closed[index - 2:index + 1]
        if third.low > first.high:
            gaps.append(FairValueGap(
                direction="bullish",
                start_time=first.timestamp,
                end_time=third.timestamp,
                lower=first.high,
                upper=third.low,
                midpoint=(first.high + third.low) / Decimal(2),
            ))
        elif third.high < first.low:
            gaps.append(FairValueGap(
                direction="bearish",
                start_time=first.timestamp,
                end_time=third.timestamp,
                lower=third.high,
                upper=first.low,
                midpoint=(third.high + first.low) / Decimal(2),
            ))

    liquidity_levels = sorted(
        {item[2] for item in indexed_swings[-20:]},
    )
    return MarketStructureAnalysis(
        symbol=symbol,
        period=period,
        provider=provider,
        trend=trend,
        latest_close=last.close,
        swings=swings[-40:],
        events=events,
        fair_value_gaps=gaps[-20:],
        liquidity_levels=liquidity_levels,
        closed_candles=len(closed),
        pivot_window=pivot_window,
        observed_at=observed_at,
        caveats=[
            "تمام محاسبات فقط از کندل‌های بسته‌شده Mark Price انجام می‌شود؛ Mark Price ممکن است با آخرین قیمت معامله متفاوت باشد.",
            "Swingها با پنجره متقارن تأیید می‌شوند و به همین دلیل با تأخیر pivot_window کندلی قابل‌شناسایی‌اند.",
            "BOS، CHoCH، Liquidity Sweep و FVG تعاریف الگوریتمی ساده و قابل‌تنظیم‌اند؛ تعاریف رایج SMC یکسان و استاندارد رسمی ندارند.",
            "این خروجی توصیفی است، پیش‌بینی قیمت یا توصیه خریدوفروش نیست؛ Order Block و امتیازدهی چندتایم‌فریمی در مرحله بعد اضافه می‌شوند.",
        ],
    )
