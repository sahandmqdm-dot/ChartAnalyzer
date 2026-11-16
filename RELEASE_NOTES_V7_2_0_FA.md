# ChartAnalyzer V7.2.0 | Quant Research Engine

## هدف نسخه

V7.2.0 ادامه مستقیم V7.1.0 است. موتور تحلیل، ساختار سیگنال و داشبورد قبلی حفظ شده‌اند؛ لایه تحقیقاتی به پروژه اضافه شده تا نتیجه هر آزمایش ثبت‌پذیر، قابل بازتولید و قابل ردگیری باشد. این نسخه سیگنال جدیدی تولید یا استراتژی موجود را بهینه نمی‌کند.

## تغییرات اصلی

### 1. Hypothesis / Experiment Registry
- دیتابیس SQLite مستقل `research_registry.db`.
- ثبت مشخصات immutable فرضیه؛ تغییر فرضیه با همان ID رد می‌شود و باید ID جدید بگیرد.
- ثبت Dataset ID، Config Hash، بازار، نماد، تایم‌فریم، Run ID، بازه نمونه، معیارها و p-value.
- اجرای مجدد همان Run ID به‌صورت idempotent ثبت می‌شود و تعداد آزمون‌ها را مصنوعی زیاد نمی‌کند.
- آزمایش‌های ناموفق و منفی حذف نمی‌شوند.
- تصحیح Benjamini-Hochberg FDR روی تمام آزمایش‌های دارای p-value در خانواده ثبت‌شده.

### 2. Declarative Hypothesis Runner
- Rule compiler امن و بدون `eval` برای قواعد Long/Flat.
- Endpoint اجرای فرضیه از پیش ثبت‌شده با Walk-Forward OOS، هزینه‌های یکسان و FDR جهانی؛ خروجی آن فقط `RESEARCH_ONLY` است.
- استفاده از ویژگی‌های علّی موجود: `score`، گروه‌های اندیکاتوری، سیگنال اندیکاتورها، مقادیر اندیکاتورها، ATR، ADX، قیمت/حجم و برچسب رژیم.
- پشتیبانی از شرط‌های `all` و `any`، مقایسه عددی، برابری رشته‌ای، `between` و Cross Above/Below.
- اجرای فرضیه ثبت‌شده با همان Backtester و Walk-Forward موجود؛ هزینه‌ها، تأخیر، ATR Stop/Target و محدودیت ساعت حفظ می‌شوند.
- فرضیه سفارشی فقط `RESEARCH_ONLY` است و به Live Gate وصل نمی‌شود.

### 3. Statistical inference
- Moving/Circular Block Bootstrap برای فاصله اطمینان Expectancy معاملات OOS.
- آزمون یک‌طرفه Block Bootstrap برای میانگین بازده خالص معاملات.
- ثبت raw p-value و FDR-adjusted q-value در Registry و Evidence Store.
- آزمون Block Signal Permutation و Matched Random Entry با حفظ طول معاملات در Research Lab برای بررسی ارتباط زمان‌بندی سیگنال با بازده آینده.
- آزمون Permutation صرفاً کنترل زمان‌بندی ورود است و جایگزین بک‌تست کامل دارای Stop/Target نیست.

### 4. Execution & Cost Model
- افزودن `execution_delay_bars` برای شبیه‌سازی تأخیر اضافی بعد از سیگنال و قبل از Next-Open Fill.
- امکان تنظیم مستقل `spread_roundtrip` و `slippage_roundtrip`؛ فیلد قدیمی `spread_slippage_roundtrip` برای سازگاری حفظ شده است.
- افزودن `market_impact_roundtrip` به‌عنوان هزینه جدا از کارمزد، اسپرد و لغزش.
- اعمال تمام هزینه‌ها در ورود، خروج و Buy & Hold benchmark.
- اجرای مجدد دقیق Walk-Forward با هزینه‌های 1.5 برابر؛ نتیجه منفی یا ناموجود مانع پذیرش می‌شود.
- Baseline تصادفی با حفظ طول دوره‌های نگهداری و آزمون Block Permutation به‌صورت تشخیص زمان‌بندی ورود اضافه شدند.
- ثبت اثر بازار و تأخیر در خروجی و Config Fingerprint.
- پیش‌فرض هر دو قابلیت جدید صفر است تا رفتار نسخه قبلی بدون تغییر بماند.

نمونه تنظیم در `config.json`:

```json
{
  "backtest": {
    "spread_roundtrip": 0.0005,
    "slippage_roundtrip": 0.0005,
    "market_impact_roundtrip": 0.0,
    "execution_delay_bars": 0
  }
}
```

این مقادیر نمونه تنظیم هستند؛ مقدار واقعی باید از شرایط اجرای سفارش در رمزینکس تخمین زده شود.

### 5. Chronological / Purged validation primitives
- تقسیم زمانی Train / Validation / Holdout با نسبت پیش‌فرض 60/20/20.
- Purged Walk-Forward Split با حذف نمونه‌هایی که افق برچسبشان با Test هم‌پوشانی دارد.
- Embargo بین پنجره‌های آزمون.
- Audit زمان دسترس‌پذیری Feature نسبت به زمان تصمیم.

### 6. Final Holdout Vault
- پیش‌نمایش Dataset Fingerprint و Strategy Config Hash.
- Seal یک‌طرفه Holdout با Dataset ID، Config Hash، منبع، نماد، تایم‌فریم و بازه زمانی.
- شروع Holdout باید بعد از آخرین نمونه ثبت‌شده همان بازار باشد.
- بک‌تست و Walk-Forward معمولی اجازه مصرف بازه Holdout مهروموم‌شده را ندارند.
- Endpoint ارزیابی نهایی فقط یک بار Claim را ثبت می‌کند و بعد از آن Holdout دوباره قابل استفاده نیست.
- نتیجه Holdout نباید برای تنظیم پارامتر یا اصلاح فرضیه استفاده شود.

**محدودیت امنیتی:** Vault یک قفل منطقی و ثبت یک‌بارمصرف در فایل محلی است، نه محیط رمزنگاری‌شده یا جداسازی فیزیکی فایل. کاربر دارای دسترسی کامل به سیستم می‌تواند فایل‌های محلی را دستکاری کند. برای استقرار سازمانی باید امضای رمزنگاری‌شده و جداسازی دسترسی فایل اضافه شود.

### 7. Robustness primitives
- پایداری همسایگی پارامترها.
- پایداری بین نمادها با حداقل تعداد نماد مستقل.
- پایداری رژیم با حداقل تعداد معامله در هر رژیم.
- این توابع تا زمانی که آزمایش‌های متناظر از قبل ثبت و اجرا نشده باشند، وضعیت `INSUFFICIENT_EVIDENCE` می‌دهند؛ از داده ناقص نتیجه مثبت ساخته نمی‌شود.

### 8. Research Lab Dashboard
- نمایش تعداد فرضیه‌ها، تعداد آزمایش‌ها، آزمایش‌های کامل/ناموفق و تعداد آزمون‌های واردشده به FDR.
- نمایش نتایج اخیر Registry و p-value خام و q-value تصحیح‌شده.
- دکمه اجرای Entry Signal Permutation.
- نمایش وضعیت Holdout Vault.
- Endpointهای API برای ثبت فرضیه، Fingerprint، Seal و ارزیابی یک‌باره Holdout.

### 9. اصلاح مهم V7.1
- Fingerprint شواهد Walk-Forward و Live Gate اکنون از یک تابع Canonical و فقط از تنظیمات واقعی فعال ساخته می‌شود.
- مقادیر گزارش‌شده در خروجی بک‌تست دیگر نمی‌توانند ناخواسته Config Fingerprint را از Live Gate جدا کنند.
- گیت همچنان Fail-Closed است.

## معیار سخت‌گیرانه گیت پذیرش

حداقل‌های اجباری نسخه 7.2:
- حداقل 200 معامله OOS.
- حداقل 5 Fold.
- حداقل 70% Fold مثبت.
- Net Profit Factor حداقل 1.20.
- Expectancy خالص مثبت.
- کران پایین Bootstrap CI 95% بزرگ‌تر از صفر.
- حداکثر Drawdown خارج از نمونه حداکثر 15%.
- FDR-adjusted q-value حداکثر 0.05.
- داده بدون Gap و Cohort Integrity برابر PASS.
- شواهد منطبق با همان منبع، نماد، تایم‌فریم و Config Fingerprint و دارای عمر معتبر.

تنظیمات کاربر می‌توانند این معیارها را سخت‌تر کنند، اما نمی‌توانند حداقل‌های اجباری را تضعیف کنند.

## APIهای جدید

- `GET /api/research-lab`
- `GET /api/research-lab/permutation`
- `GET /api/research-lab/fingerprint`
- `POST /api/research-lab/hypotheses`
- `POST /api/research-lab/run-hypothesis`
- `GET /api/research-lab/random-entry`
- `GET /api/research-lab/split-preview`
- `POST /api/research-lab/robustness`
- `POST /api/research-lab/holdout/seal`
- `POST /api/research-lab/holdout/evaluate`

## فایل‌های جدید

- `research_registry.py`
- `research_statistics.py`
- `research_protocol.py`
- `research_robustness.py`
- `holdout_vault.py`

## سازگاری و مهاجرت

- `config.json` و `agent_journal.db` شخصی خود را حفظ کنید.
- `research_registry.db` و `strategy_validation_evidence.json` در اجرای نسخه جدید به‌صورت محلی ساخته می‌شوند.
- شواهد V7.1 به‌دلیل تغییر نسخه و Fingerprint به‌طور خودکار به‌عنوان شواهد V7.2 پذیرفته نمی‌شوند؛ Walk-Forward جدید اجرا کنید.
- اگر `config.json` شما مقادیر قدیمی Gate داشته باشد، حداقل‌های سخت‌گیرانه V7.2 همچنان اعمال می‌شوند.

## اعتبارسنجی توسعه

- تست‌های خودکار: 214 passed.
- Python compileall: موفق.
- آزمون‌های جدید: Registry immutability/idempotency، FDR، Block Bootstrap، Permutation، Holdout one-time claim، Holdout overlap guard، Purged CV، Feature availability audit، Parameter/Symbol/Regime robustness، execution delay و market impact.

این تست‌ها صحت نرم‌افزار و قراردادهای محاسباتی را بررسی می‌کنند؛ **اثبات سودآوری استراتژی یا صحت هزینه‌های واقعی رمزینکس نیستند.**
