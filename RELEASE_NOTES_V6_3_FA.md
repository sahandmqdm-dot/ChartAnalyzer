# ChartAnalyzer V6.3.0 — Futures Isolated-Margin Engine

## قابلیت‌های جدید
- محاسبه Initial Margin بر اساس Notional و Leverage.
- تخمین قیمت لیکوییدیشن برای پوزیشن‌های Long و Short خطی با مارجین Isolated.
- اثر مارجین افزوده بر دورتر شدن آستانه لیکوییدیشن.
- لحاظ کردن Maintenance Margin و ذخیره محافظه‌کارانه Liquidation Fee در رابطه تخمین.
- محاسبه اختیاری PnL شناور، Equity، نسبت ذخیره مارجین و فاصله تا لیکوییدیشن با دریافت Mark Price.
- وضعیت سلامت: `healthy`، `warning`، `critical` و `liquidation_threshold_reached`.
- API: `GET /api/margin` و ماشین‌حساب داشبورد.

## پیکربندی
در `config.json`:

```json
"futures_margin": {
  "margin_mode": "isolated",
  "maintenance_margin_rate": 0.005,
  "liquidation_fee_rate": 0.005,
  "max_leverage": 125,
  "warning_distance_pct": 5.0,
  "critical_distance_pct": 2.0
}
```

نرخ‌ها به‌صورت کسر اعشاری هستند؛ `0.005` یعنی `0.5%`. مقدار Quantity بر حسب واحد دارایی پایه است. برای نمونه، در قرارداد BTC با قیمت بر حسب USDT، Quantity برابر `0.01` یعنی `0.01 BTC` و خروجی‌های پولی بر حسب USDT خواهند بود.

## نمونه API

```text
GET /api/margin?market_type=futures&side=long&entry_price=68000&quantity=0.01&leverage=5&mark_price=66000
```

پارامترهای نرخ مارجین نگهداری و کارمزد لیکوییدیشن، در صورت حذف، از `config.json` خوانده می‌شوند. مقدار صفر برای اهرم یعنی استفاده از اهرم پیکربندی‌شده بک‌تست. در API مقدار `-1` برای نرخ‌ها به معنی استفاده از تنظیمات است.

## محدودیت مدل و ریسک
- فقط قرارداد خطی و تسویه‌شونده با ارز مظنه، با حالت Isolated.
- Cross Margin، وثیقه مشترک، پلکان Maintenance Margin، ریسک‌تیر صرافی، Mark Price اختصاصی صرافی، تأخیر، لغزش، کارمزدهای واقعی لیکوییدیشن و Funding انباشته مدل نمی‌شوند.
- نرخ Liquidation Fee در این نسخه به‌عنوان ذخیره محافظه‌کارانه به Maintenance Margin اضافه می‌شود؛ این قرارداد دقیق همه صرافی‌ها نیست.
- قیمت لیکوییدیشن تخمینی است و به معنی قیمت قطعی اجرای خروج نیست.
- این ماژول جدا از Backtest تاریخی است؛ بک‌تست هنوز Liquidation و Margin Call را شبیه‌سازی نمی‌کند.

## آزمون‌ها
تست‌های مستقل فرمول Long/Short، اثر وثیقه افزوده، وضعیت سلامت مارجین، اعتبارسنجی ورودی، API و نمایش داشبورد به مجموعه آزمون افزوده شدند.
