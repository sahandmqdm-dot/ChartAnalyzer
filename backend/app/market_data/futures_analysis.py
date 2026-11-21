from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.market_data.futures_history import FuturesHistory
from app.market_data.mark_price import MarkPriceCandle


class DerivativesSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    label: str
    direction: str
    severity: str
    explanation: str


class FuturesAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True)

    symbol: str
    period: str
    price_change_pct: Decimal | None
    open_interest_change_pct: Decimal | None
    latest_funding_rate: Decimal | None
    latest_long_short_ratio: Decimal | None
    positioning: str
    funding_bias: str
    squeeze_risk: str
    confidence: str
    signals: list[DerivativesSignal]
    caveats: list[str]
    observed_at: datetime


def _change_pct(previous: Decimal, latest: Decimal) -> Decimal | None:
    if previous <= 0:
        return None
    return (latest - previous) / previous * Decimal(100)


def analyze_futures_context(
    history: FuturesHistory,
    candles: list[MarkPriceCandle],
) -> FuturesAnalysis:
    """Create descriptive, rule-based futures context from recent series.

    Price is supplied by the exchange's futures Mark Price OHLC feed.
    """
    closed = sorted((c for c in candles if c.is_closed), key=lambda c: c.timestamp)
    price_change = (
        _change_pct(closed[-2].close, closed[-1].close) if len(closed) >= 2 else None
    )
    oi = history.open_interest
    oi_change = (
        _change_pct(oi[-2].value, oi[-1].value)
        if len(oi) >= 2
        else None
    )
    funding = history.funding_rate[-1].value if history.funding_rate else None
    ratio = history.long_short_ratio[-1].value if history.long_short_ratio else None

    signals: list[DerivativesSignal] = []
    positioning = "insufficient_data"
    if price_change is not None and oi_change is not None:
        if price_change > 0 and oi_change > 0:
            positioning = "long_and_short_build_up_with_rising_price"
            signals.append(DerivativesSignal(
                code="price_up_oi_up", label="قیمت و Open Interest صعودی",
                direction="bullish", severity="moderate",
                explanation="افزایش قیمت همراه با رشد OI با بازشدن موقعیت‌های جدید سازگار است؛ به‌تنهایی جهت خالص پوزیشن‌ها را مشخص نمی‌کند.",
            ))
        elif price_change < 0 and oi_change > 0:
            positioning = "short_build_up_with_falling_price"
            signals.append(DerivativesSignal(
                code="price_down_oi_up", label="قیمت نزولی و OI صعودی",
                direction="bearish", severity="moderate",
                explanation="این ترکیب با افزایش موقعیت‌های باز در جریان افت قیمت سازگار است و می‌تواند نشانه تقویت فشار فروش باشد.",
            ))
        elif price_change > 0 and oi_change < 0:
            positioning = "short_covering_or_deleveraging"
            signals.append(DerivativesSignal(
                code="price_up_oi_down", label="رشد قیمت همراه با افت OI",
                direction="bullish", severity="low",
                explanation="ممکن است بخشی از رشد ناشی از بسته‌شدن موقعیت‌های فروش یا کاهش اهرم باشد، نه ورود موقعیت‌های جدید.",
            ))
        elif price_change < 0 and oi_change < 0:
            positioning = "long_deleveraging_or_liquidation"
            signals.append(DerivativesSignal(
                code="price_down_oi_down", label="افت قیمت و Open Interest",
                direction="bearish", severity="low",
                explanation="این ترکیب با بسته‌شدن موقعیت‌ها و کاهش اهرم در زمان افت قیمت سازگار است؛ وقوع لیکوییدیشن از این داده‌ها اثبات نمی‌شود.",
            ))

    funding_bias = "unknown"
    if funding is not None:
        threshold = Decimal("0.0005")
        if funding >= threshold:
            funding_bias = "positive_crowding_risk"
            signals.append(DerivativesSignal(
                code="funding_high_positive", label="Funding مثبت و نسبتاً بالا",
                direction="crowded_long", severity="moderate",
                explanation="پرداخت Funding مثبت می‌تواند نشان‌دهنده تمایل بیشتر به موقعیت‌های Long باشد؛ آستانه ثابت، وابسته به قرارداد و رژیم بازار نیست.",
            ))
        elif funding <= -threshold:
            funding_bias = "negative_crowding_risk"
            signals.append(DerivativesSignal(
                code="funding_high_negative", label="Funding منفی و نسبتاً پایین",
                direction="crowded_short", severity="moderate",
                explanation="Funding منفی می‌تواند با تمایل بیشتر به موقعیت‌های Short سازگار باشد؛ این وضعیت به‌تنهایی سیگنال برگشت قیمت نیست.",
            ))
        else:
            funding_bias = "near_neutral"
            signals.append(DerivativesSignal(
                code="funding_neutral", label="Funding نزدیک محدوده خنثی",
                direction="neutral", severity="low",
                explanation="Funding در محدوده آستانه ساده این موتور قرار دارد.",
            ))

    if ratio is not None:
        signals.append(DerivativesSignal(
            code="long_short_context",
            label="نسبت حساب‌های Long/Short",
            direction="long_crowded" if ratio > Decimal("1.2") else "short_crowded" if ratio < Decimal("0.8") else "balanced",
            severity="low",
            explanation="این نسبت تعداد حساب‌ها را بازتاب می‌دهد، نه اندازه پوزیشن‌ها یا ارزش دلاری آن‌ها.",
        ))

    squeeze_risk = "not_assessed"
    if funding is not None and oi_change is not None and price_change is not None:
        if funding >= Decimal("0.001") and oi_change > 0 and price_change < 0:
            squeeze_risk = "long_squeeze_conditions"
        elif funding <= Decimal("-0.001") and oi_change > 0 and price_change > 0:
            squeeze_risk = "short_squeeze_conditions"
        else:
            squeeze_risk = "no_strong_rule_based_condition"
    confidence = (
        "medium" if price_change is not None and oi_change is not None and funding is not None and ratio is not None
        else "low"
    )
    caveats = [
        "تغییر قیمت از کندل‌های Mark Price قرارداد خطی محاسبه شده است؛ این قیمت مرجع محاسبات مارجین است و با آخرین قیمت معامله یکسان نیست.",
        "Open Interest، Funding و نسبت Long/Short از تعریف‌ها و پنجره‌های زمانی مختص هر صرافی پیروی می‌کنند.",
        "برچسب‌های Long/Short build-up و squeeze فقط تفسیر احتمالی‌اند؛ این داده‌ها سمت دقیق همه پوزیشن‌ها یا وقوع لیکوییدیشن را ثابت نمی‌کنند.",
        "آستانه Funding ثابت و اکتشافی است و هنوز برای هر نماد، صرافی و رژیم بازار کالیبره نشده است.",
    ]
    return FuturesAnalysis(
        symbol=history.symbol,
        period=history.period,
        price_change_pct=price_change,
        open_interest_change_pct=oi_change,
        latest_funding_rate=funding,
        latest_long_short_ratio=ratio,
        positioning=positioning,
        funding_bias=funding_bias,
        squeeze_risk=squeeze_risk,
        confidence=confidence,
        signals=signals,
        caveats=caveats,
        observed_at=history.observed_at,
    )
