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
    status: str
    mitigation_time: datetime | None = None
    invalidation_time: datetime | None = None


class OrderBlock(BaseModel):
    model_config = ConfigDict(frozen=True)

    direction: str
    start_time: datetime
    break_time: datetime
    lower: Decimal
    upper: Decimal
    source_swing_level: Decimal
    status: str
    mitigation_time: datetime | None = None
    invalidation_time: datetime | None = None


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
    order_blocks: list[OrderBlock]
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



def _track_fvg(
    *,
    candles: list[MarkPriceCandle],
    creation_index: int,
    direction: str,
    lower: Decimal,
    upper: Decimal,
    start_time: datetime,
    end_time: datetime,
) -> FairValueGap:
    status = "active"
    mitigation_time = None
    invalidation_time = None
    for candle in candles[creation_index + 1:]:
        if direction == "bullish":
            if candle.close < lower:
                status = "invalidated"
                invalidation_time = candle.timestamp
                break
            if candle.low <= lower:
                status = "filled"
                mitigation_time = mitigation_time or candle.timestamp
                break
            if candle.low < upper:
                status = "partially_filled"
                mitigation_time = mitigation_time or candle.timestamp
        else:
            if candle.close > upper:
                status = "invalidated"
                invalidation_time = candle.timestamp
                break
            if candle.high >= upper:
                status = "filled"
                mitigation_time = mitigation_time or candle.timestamp
                break
            if candle.high > lower:
                status = "partially_filled"
                mitigation_time = mitigation_time or candle.timestamp
    return FairValueGap(
        direction=direction,
        start_time=start_time,
        end_time=end_time,
        lower=lower,
        upper=upper,
        midpoint=(lower + upper) / Decimal(2),
        status=status,
        mitigation_time=mitigation_time,
        invalidation_time=invalidation_time,
    )


def _detect_order_blocks(
    *,
    candles: list[MarkPriceCandle],
    indexed_swings: list[tuple[int, str, Decimal]],
    pivot_window: int,
) -> list[OrderBlock]:
    blocks: list[OrderBlock] = []
    for pivot_index, kind, level in indexed_swings:
        confirmation_index = pivot_index + pivot_window
        break_index = None
        direction = "bullish" if kind == "high" else "bearish"
        for index in range(confirmation_index + 1, len(candles)):
            previous, current = candles[index - 1], candles[index]
            crossed = (
                previous.close <= level < current.close
                if direction == "bullish"
                else previous.close >= level > current.close
            )
            if crossed:
                break_index = index
                break
        if break_index is None:
            continue

        search_start = max(confirmation_index, break_index - 5)
        opposing_index = next(
            (
                index
                for index in range(break_index - 1, search_start - 1, -1)
                if (
                    candles[index].close < candles[index].open
                    if direction == "bullish"
                    else candles[index].close > candles[index].open
                )
            ),
            None,
        )
        if opposing_index is None:
            continue

        source = candles[opposing_index]
        status = "active"
        mitigation_time = None
        invalidation_time = None
        for later in candles[break_index + 1:]:
            if direction == "bullish":
                if later.close < source.low:
                    status = "invalidated"
                    invalidation_time = later.timestamp
                    break
                if later.low <= source.high:
                    status = "mitigated"
                    mitigation_time = mitigation_time or later.timestamp
            else:
                if later.close > source.high:
                    status = "invalidated"
                    invalidation_time = later.timestamp
                    break
                if later.high >= source.low:
                    status = "mitigated"
                    mitigation_time = mitigation_time or later.timestamp

        blocks.append(OrderBlock(
            direction=direction,
            start_time=source.timestamp,
            break_time=candles[break_index].timestamp,
            lower=source.low,
            upper=source.high,
            source_swing_level=level,
            status=status,
            mitigation_time=mitigation_time,
            invalidation_time=invalidation_time,
        ))
    return blocks


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
            order_blocks=[],
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
    if latest_high and closed[-2].close <= latest_high[2] < last.close:
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
    elif latest_low and closed[-2].close >= latest_low[2] > last.close:
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
        first, _middle, third = closed[index - 2:index + 1]
        if third.low > first.high:
            gaps.append(_track_fvg(
                candles=closed,
                creation_index=index,
                direction="bullish",
                lower=first.high,
                upper=third.low,
                start_time=first.timestamp,
                end_time=third.timestamp,
            ))
        elif third.high < first.low:
            gaps.append(_track_fvg(
                candles=closed,
                creation_index=index,
                direction="bearish",
                lower=third.high,
                upper=first.low,
                start_time=first.timestamp,
                end_time=third.timestamp,
            ))

    order_blocks = _detect_order_blocks(
        candles=closed,
        indexed_swings=indexed_swings,
        pivot_window=pivot_window,
    )
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
        order_blocks=order_blocks[-20:],
        liquidity_levels=liquidity_levels,
        closed_candles=len(closed),
        pivot_window=pivot_window,
        observed_at=observed_at,
        caveats=[
            "تمام محاسبات فقط از کندل‌های بسته‌شده Mark Price انجام می‌شود؛ Mark Price ممکن است با آخرین قیمت معامله متفاوت باشد.",
            "Swingها با پنجره متقارن تأیید می‌شوند و به همین دلیل با تأخیر pivot_window کندلی قابل‌شناسایی‌اند.",
            "BOS، CHoCH، Liquidity Sweep و FVG تعاریف الگوریتمی ساده و قابل‌تنظیم‌اند؛ تعاریف رایج SMC یکسان و استاندارد رسمی ندارند.",
            "FVG و Order Block با قواعد هندسی و شکست ساختار تعریف شده‌اند؛ این تعاریف، استاندارد رسمی و یگانه SMC نیستند و وجود سفارش نهادی را اثبات نمی‌کنند.",
            "Mitigation به معنی ورود قیمت به محدوده است؛ Filled یعنی رسیدن قیمت تا لبه دورتر ناحیه، و Invalidated یعنی بسته‌شدن قیمت در آن سوی لبه دورتر.",
            "Order Block در این نسخه آخرین کندل مخالف در حداکثر پنج کندل پیش از شکست Swing تأییدشده است؛ محدوده آن کل High/Low همان کندل است."
        ],
    )
