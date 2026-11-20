# ChartAnalyzer V7.3.0 | Futures Market Intelligence & Canonical Versioning

## هدف نسخه

V7.3.0 قابلیت تحقیقاتی Futures را به داشبورد اضافه می‌کند و پراکندگی نسخه‌های سخت‌کدشده را برطرف می‌کند. نسخه برنامه فقط در `version.py` تعریف می‌شود؛ APIها، گزارش‌ها، شناسه MCP و هویت پژوهش از همان مقدار استفاده می‌کنند.

## قابلیت‌های Futures

- سناریوهای تحقیقاتی LONG و SHORT برای قراردادهای دائمی Binance USD-M.
- استفاده از کندل‌های بسته‌شده و نمایش منشأ داده OHLCV.
- دریافت Funding Rate، Mark Price، Index Price و زمان Funding بعدی.
- دریافت تاریخچه Open Interest و محاسبه تغییر نسبت به یک baseline زمانی.
- دریافت یک REST Order Book snapshot با Best Bid/Ask، spread و عدم‌توازن حجم 20 سطح نخست.
- دریافت نمونه محدود Aggregate Trades و محاسبه حجم taker buy/sell و جریان خالص نمونه.
- نمایش سناریوهای Entry/Stop/Target و اطلاعات مشتقات در داشبورد.

## محدودیت‌ها و ایمنی

- سناریوها WATCH ONLY هستند؛ تصمیم اجرایی و مجوز استقرار Futures صادر نمی‌شود.
- امتیاز جهت‌گیری heuristic است و احتمال کالیبره‌شده محسوب نمی‌شود.
- Order Book فعلی یک REST snapshot است، نه جریان پیوسته depth-diff با بازسازی sequence.
- Aggregate Trades یک نمونه محدود اخیر است، نه آرشیو کامل tick-by-tick.
- Order Book و Trade Flow فعلاً فقط context هستند و در امتیاز جهت‌گیری دخالت ندارند.
- اتصال فعلی فقط به endpointهای عمومی است؛ هیچ حساب یا سفارش واقعی استفاده نمی‌شود.

## یکپارچگی نسخه

- مرجع یگانه نسخه: `version.py`.
- فایل `VERSION` برای نمایش نسخه در ابزارهای مخزن با همان مقدار همگام است.
- API تشخیصی: `GET /api/version`.
- نسخه در provenance، Walk-Forward، Trade Failure Intelligence و MCP clientInfo از یک constant خوانده می‌شود.
- افزایش نسخه برنامه باعث تغییر fingerprint پژوهشی می‌شود؛ شواهد قدیمی با نسخه قبلی به‌طور خودکار برای نسخه جدید معتبر تلقی نمی‌شوند.

## اعتبارسنجی

آزمون‌های CI باید روی Python 3.12 و 3.13 اجرا شوند. این یادداشت به‌تنهایی ادعای موفقیت تست یا سودآوری بازار نیست.
