# Futures Risk Engine v3

## هدف

ارزیابی یک پوزیشن خطی Isolated Futures با ترکیب Mark Price تازه، Risk Tierهای دریافت‌شده از صرافی و هشدار قابل‌فهم. این API فقط خواندنی/تحلیلی است و سفارش ایجاد یا تغییر نمی‌دهد.

## Endpoint

`POST /api/v1/derivatives/risk-assessment/live`

این مسیر نیازمند نشست احراز هویت معتبر است.

نمونه درخواست:

```json
{
  "symbol": "BTCUSDT",
  "side": "long",
  "entry_price": "65000",
  "quantity": "0.01",
  "leverage": "10",
  "extra_margin": "0",
  "estimated_close_fee_rate": "0.0005"
}
```

درخواست عمداً Risk Tier و Mark Price دستی قبول نمی‌کند. سرویس:
1. Mark Price و timestamp مشاهده را از snapshot بازار Futures صرافی می‌گیرد.
2. جدول Risk Limit نماد را از API عمومی Bybit Linear می‌گیرد و تا ۵ دقیقه cache می‌کند.
3. تخمین Liquidation را با maintenance margin و cumulative deduction همان Tierها محاسبه می‌کند.
4. فاصله signed تا Liquidation را از Mark Price به درصد تبدیل و سطح هشدار را تعیین می‌کند.

## سطح‌های هشدار

| سطح | شرط فاصله تا Liquidation |
|---|---:|
| Normal | بیشتر از ۱۰٪ |
| Warning | بیشتر از ۵٪ تا ۱۰٪ |
| Critical | بیشتر از ۱٪ تا ۵٪ |
| Liquidation نزدیک | ۱٪ یا کمتر؛ شامل فاصله صفر و منفی |

این مرزها فعلاً آستانه‌های محصول هستند، نه پارامترهای صرافی. فاصله منفی یعنی Mark Price از قیمت Liquidation تخمینی عبور کرده است.

## وضعیت پشتیبانی صرافی

- **Bybit Linear:** Mark Price snapshot و Risk Tierهای واقعی از endpoint عمومی دریافت می‌شوند.
- **Binance USDⓈ-M:** Mark Price snapshot موجود است؛ اما دریافت leverage brackets به API امضاشده حساب نیاز دارد. چون تنظیمات امن API key/secret و امضای درخواست در این نسخه اضافه نشده، مسیر ارزیابی زنده با Binance عمداً HTTP 501 می‌دهد و Tier دستی را به‌جای داده واقعی جا نمی‌زند.

## معنای «زنده» و محدودیت‌ها

در v3، Mark Price یک snapshot تازه هنگام فراخوانی API است؛ این هنوز WebSocket push/streaming برای مانیتورینگ پیوسته یا ارسال هشدار مستقل از درخواست کاربر نیست. timestamp و سن snapshot در خروجی آمده‌اند. جدول Risk Tier با TTL پنج دقیقه cache می‌شود و timestamp دریافت آن جداگانه برمی‌گردد.

محاسبه فعلی برای قرارداد خطی quote-settled و Isolated Margin است. Cross/Portfolio Margin، funding cashflows، open orders، feeهای اختصاصی لیکوییدیشن، تخفیف fee-token و رفتار دقیق موتور liquidation صرافی شبیه‌سازی نمی‌شوند. خروجی یک estimate است، نه تضمین قیمت لیکوییدیشن صرافی یا توصیه معاملاتی.

## Endpointهای مرتبط

- `GET /api/v1/derivatives/overview?symbol=BTCUSDT` — snapshot شامل Mark Price
- `GET /api/v1/derivatives/risk-tiers?symbol=BTCUSDT` — جدول Risk Tier
- `POST /api/v1/derivatives/risk-estimate` — تخمین با ورودی tierها (برای تست/تحلیل)
- `POST /api/v1/derivatives/risk-assessment/live` — جریان یکپارچه v3
