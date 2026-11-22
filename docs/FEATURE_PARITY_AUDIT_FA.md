# ممیزی تطبیق قابلیت‌ها: main در برابر chartanalyzer-next-foundation

تاریخ ممیزی: 2026-11-22  
شاخه مرجع: `main`  
شاخه هدف: `chartanalyzer-next-foundation`  
هدف: جلوگیری از حذف ناخواسته قابلیت‌های ارزشمند نسخه قبلی هنگام توسعه معماری Next.

## خلاصه اجرایی

بر اساس مقایسه GitHub، شاخه Next نسبت به main تعداد 479 کامیت جلوتر و صفر کامیت عقب‌تر است؛ نقطه مبنای مشترک هر دو `3baa4314dd434c9ab5125dc22ff13e965c6d5e43` است. مقایسه فایل‌ها 300 مسیر را نشان می‌دهد: 105 فایل اضافه، 191 فایل حذف و 4 فایل تغییر یافته. این آمار به معنی انتقال کامل قابلیت‌ها نیست؛ ساختار قدیمی عمدتاً حذف و برنامه جدید در مسیرهای `backend/` و `frontend/` ساخته شده است.

**نتیجه معماری:** Next پایه تمیزتر و قابل توسعه‌تری دارد، اما در وضعیت فعلی جایگزین کامل main نیست. main باید تا پایان انتقال قابلیت‌های منتخب حفظ شود.

## راهنمای وضعیت

- **موجود:** قابلیت یا معادل عملی آن در ساختار Next قابل مشاهده است.
- **جزئی/نیازمند توسعه:** بخشی از قابلیت در Next هست، اما هم‌ارزی کامل اثبات نشده است.
- **منتقل‌نشده:** در ساختار و فایل‌های فعلی Next معادل آشکاری مشاهده نشد.
- **تصمیم محصول:** باید آگاهانه درباره انتقال، بازطراحی یا کنارگذاری تصمیم بگیریم.

این ممیزی بر اساس فهرست فایل‌ها، READMEها، مستندات معماری و تست‌های دو شاخه تهیه شده است. وضعیت «منتقل‌نشده» به معنی بررسی خط‌به‌خط تمام منطق قدیمی نیست؛ پیش از انتقال هر قابلیت باید قراردادها و رفتار دقیق آن از روی کد و تست‌های main بررسی شود.

## ماتریس قابلیت‌ها

| حوزه | شواهد در main | وضعیت در Next | تصمیم پیشنهادی |
|---|---|---|---|
| معماری ماژولار و جداسازی دامنه | ساختار قدیمی عمدتاً فایل‌های ریشه | موجود: backend/app و لایه‌های market_data/backtest | حفظ معماری Next |
| API و سرویس‌دهی | app.py | موجود: FastAPI و routeهای دامنه‌ای | حفظ و تکمیل |
| رابط کاربری | داشبورد محلی در app.py | جزئی: React workspace پایه | توسعه UI بر اساس APIهای پایدار |
| احراز هویت | security.py و تست‌های امنیت | موجود: signed-cookie، CSRF logout و throttling پایه | بازبینی امنیتی و تقویت |
| داده Spot از Binance و Bybit | market_data_hub.py و providerهای قبلی | موجود: BinanceSpotAdapter و BybitSpotAdapter | حفظ |
| داده Futures و قراردادهای خطی | futures_market_snapshots.py، funding_open_interest.py | موجود: overview/history/mark price و adapterهای Futures | تکمیل پوشش و کیفیت داده |
| داده OHLCV تاریخی و pagination | market_data_hub.py و archive sync | جزئی: adapterهای Spot و Futures جدید | مقایسه سقف‌ها، gap handling و provenance |
| Funding و Open Interest | funding_open_interest.py | موجود/جزئی: historical derivatives series | تطبیق فرمت‌ها و اعتبارسنجی |
| Mark Price | futures_exchange_rules.py و ابزارهای Futures | موجود: historical Mark Price و استفاده در liquidation | حفظ و افزودن پایش زنده فقط با منبع معتبر |
| Liquidation stream و ذخیره‌سازی | ابزارهای microstructure و futures | موجود: stream و PostgreSQL persistence برای رویدادها | حفظ؛ تکمیل observability و retention |
| محاسبه مارجین و لیکوییدیشن | margin_engine.py، tiered_margin_engine.py | جزئی: isolated risk، liquidation و tier estimates | انتقال قواعد صرافی با منبع رسمی و تست |
| Backtesting پایه | backtest_engine.py | موجود: موتور Spot و Futures جدید | حفظ و تطبیق رفتار |
| Futures accounting/lifecycle | مدل‌های قبلی Futures | موجود: accounting، funding، fees و liquidation lifecycle | ادامه تست‌های سناریویی |
| Signal/Trend/Market Structure | trend_engine.py، futures_signal_engine.py | جزئی: market_structure و multi_timeframe؛ هم‌ارزی سیگنال اثبات نشده | استخراج قرارداد و انتقال انتخابی |
| Statistical Edge | statistical_edge.py، effective_sample_size.py | منتقل‌نشده | اولویت بالا برای بازطراحی |
| Forecast و توزیع احتمالاتی | forecast_engine.py، predictive_intelligence.py | منتقل‌نشده | اولویت بالا؛ حفظ برچسب‌های عدم قطعیت |
| Forecast calibration/validation | forecast_validation.py | منتقل‌نشده | انتقال همراه با baseline و calibration tests |
| Walk-Forward و OOS | walk_forward.py | منتقل‌نشده | اولویت بسیار بالا برای اعتبارسنجی استراتژی |
| Research statistics و bootstrap | research_statistics.py | منتقل‌نشده | انتقال پس از قرارداد داده و run identity |
| Overfitting / CSCV / Deflated Sharpe | advanced_overfit_diagnostics.py | منتقل‌نشده | انتقال به ماژول Research |
| Experiment registry و provenance | research_registry.py، research_provenance.py | منتقل‌نشده | اولویت بالا؛ ثبت lineage و بازتولیدپذیری |
| Holdout و prospective evaluation | holdout_vault.py، prospective_holdout.py | منتقل‌نشده | انتقال مرحله‌ای با جلوگیری از leakage |
| Strategy validation / deployment gate | strategy_validation.py | منتقل‌نشده | قبل از هر ادعای readiness لازم است |
| Microstructure / Order Book | binance_orderbook.py و stream collector | منتقل‌نشده | بازطراحی به‌صورت bounded context مستقل |
| Point-in-Time data integrity | binance_pit_vault.py و PIT integration | منتقل‌نشده | اولویت بالا برای پژوهش بدون look-ahead |
| Multi-asset / US equities | multi_asset/ و equity_market_rules.py | منتقل‌نشده | فاز بعدی؛ ابتدا هسته Crypto پایدار شود |
| بازارهای ایرانی | iran_crypto_market.py و iran_crypto_mcp.py | منتقل‌نشده | قابلیت اختیاری؛ adapter جدا و بدون ادغام خاموش |
| CI و کیفیت کد | workflow چندمحوره، pytest، Bandit، pip-audit | جزئی: pytest، Ruff و frontend build | ارتقا به ماتریس Python، امنیت و تست‌های ویندوز |
| مستندات و راهنمای عملیاتی | نسخه‌ها، runbookها و release notes متعدد | جزئی: README و docs پایه | مستندسازی API، backup، restore، threat model |
| استقرار | اسکریپت‌های Windows و tooling قدیمی | جزئی: Docker Compose و PostgreSQL | حفظ مسیر Docker و افزودن راهنمای Windows |

## قابلیت‌هایی که در Next واقعاً ساخته شده‌اند

این موارد صرفاً انتقال اسمی نیستند و در شاخه Next ساختار اختصاصی و تست‌های مرتبط دارند:

1. دامنه OHLCV با Decimal، timestampهای UTC و اعتبارسنجی candle.
2. adapterهای Spot برای Binance و Bybit و انتخاب provider.
3. مشتقات خطی: overview، history، funding، open interest و Mark Price.
4. استریم رویدادهای liquidation و repository پایدار PostgreSQL.
5. مدل Futures backtest با funding، isolated margin، liquidation و settlement.
6. برآورد ریسک Futures با tier، bankruptcy estimate و فاصله Mark Price تا liquidation.
7. ساختار احراز هویت، تنظیمات، migration و Docker Compose.
8. React + TypeScript workspace پایه.

## شکاف‌های مهم و ترتیب انتقال

### P0 — پیش از معرفی Next به‌عنوان جایگزین main

- اجرای کامل تست‌های backend و frontend در CI و ثبت نتیجه واقعی.
- افزایش CI به security audit، dependency audit و تست Windows متناسب با نیاز.
- تثبیت API contracts، error handling، rate limits و observability.
- تهیه threat model و بررسی امنیت مستقل؛ Next طبق README هنوز Production-hardened نیست.
- تکمیل Feature Parity برای Backtest و Futures با سناریوهای مرجع.

### P1 — حفظ اعتبار پژوهش و جلوگیری از سیگنال گمراه‌کننده

- انتقال Statistical Edge و effective sample size.
- انتقال Forecast، calibration و validation baselines.
- انتقال Walk-Forward، purged/embargoed split و OOS reporting.
- انتقال research provenance، experiment registry و reproducible run identity.
- انتقال Holdout و prospective evaluation با کنترل leakage.
- انتقال Strategy Validation و fail-closed eligibility gate.

### P2 — توسعه داده و پوشش بازار

- انتقال Point-in-Time microstructure، order book و collector telemetry.
- افزودن multi-asset و US equities پس از تثبیت هسته.
- افزودن Iranian exchange adapters به‌صورت اختیاری و ایزوله.
- افزودن runbooks، backup/restore، نسخه‌بندی و انتشار.

## قواعد انتقال

1. کد قدیمی را یک‌باره کپی نکنیم؛ ابتدا ورودی/خروجی، invariantها، وابستگی‌ها و تست‌های آن ثبت شود.
2. منطق دامنه از FastAPI، SQLAlchemy، exchange SDK و UI مستقل بماند.
3. برای هر قابلیت انتقالی، تست‌های رفتاری از main به Next بازنویسی شوند؛ تست قدیمی بدون تطبیق قراردادها کپی نشود.
4. داده خام و خروجی پژوهشی باید provenance و timestamp معتبر داشته باشند.
5. هر قابلیت ناقص باید در API و UI با وضعیت واضح نمایش داده شود؛ خروجی ناقص نباید به‌عنوان تحلیل معتبر عرضه شود.
6. ادغام main در Next یا تغییر main فقط پس از بررسی جداگانه و تأیید صریح انجام شود.

## معیار پایان مهاجرت

Next زمانی می‌تواند نامزد جایگزینی main شود که:
- تمام قابلیت‌های P0 و P1 یا منتقل شده باشند یا تصمیم حذف مستند داشته باشند.
- تست‌های واحد، integration، regression و CI روی محیط‌های هدف سبز باشند.
- رفتار Backtest/Futures با fixtureهای ثابت و خروجی‌های مرجع مقایسه شده باشد.
- امنیت، مدیریت secrets، session lifecycle، rate limiting و data retention بررسی شده باشد.
- راهنمای نصب، ارتقا، backup/restore و rollback وجود داشته باشد.
- هیچ ادعای سودآوری یا آمادگی Production بدون شواهد مستقل مطرح نشود.

## منابع داخلی

- README و docs/ARCHITECTURE.md در شاخه main.
- README و docs/ARCHITECTURE.md در شاخه chartanalyzer-next-foundation.
- فهرست تست‌های tests/ در main و backend/tests در Next.
- مقایسه GitHub: https://github.com/sahandmqdm-dot/ChartAnalyzer/compare/main...chartanalyzer-next-foundation

## محدودیت این ممیزی

این سند یک ممیزی ساختاری و اولویت‌بندی انتقال است، نه تأیید صحت همه الگوریتم‌ها. هنوز اجرای محلی کامل pytest، تست‌های integration، security audit و تطبیق خروجی عددی دو موتور Backtest انجام نشده است. هیچ‌کدام از این موارد در این سند به‌عنوان موفق گزارش نمی‌شوند.
