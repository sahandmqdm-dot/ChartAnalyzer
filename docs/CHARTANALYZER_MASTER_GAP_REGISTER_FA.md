# ChartAnalyzer Master Gap Register (FA)

**وضعیت سند:** ممیزی مبتنی بر وضعیت مخزن در 2026-11-21  
**مبنای ممیزی:** شاخه main با commit مبنا `5e2013744a2a510e114ba78e029d57cebb6be92e`  
**اصل حاکم:** وجود کد، تست سبز یا نمایش داشبورد به معنی صحت پیش‌بینی، قابلیت معامله یا سودآوری نیست.

این سند فهرست جامعِ مواردی است که از مستندات و فایل‌های قابل‌مشاهده مخزن قابل اثبات‌اند. مواردی که هنوز با اجرای انتها‌به‌انتها بررسی نشده‌اند با «نیازمند ممیزی» مشخص شده‌اند؛ آن‌ها را نباید باگ قطعی یا قابلیت تکمیل‌شده معرفی کرد.

## 1) هدف نهایی محصول

ChartAnalyzer باید به یک **Evidence-Driven Market Intelligence & Decision Platform** تبدیل شود، نه کارخانه تولید سیگنال.

خروجی مطلوب برای هر دارایی/بازار/افق:

1. بازسازی وضعیت بازار فقط با اطلاعاتی که تا زمان تصمیم واقعاً در دسترس بوده‌اند.
2. ساخت توزیع احتمالاتی و سناریوهای آینده، همراه با عدم‌قطعیت و baseline.
3. توضیح نیروهای پشت حرکت: ساختار قیمت، جریان معاملات، نقدشوندگی، مشتقات، رژیم و رویدادها.
4. محاسبه امکان‌پذیری اقتصادی تصمیم پس از تمام هزینه‌ها و محدودیت‌های واقعی بازار.
5. صدور یکی از تصمیم‌های قابل‌ردیابی: PAPER_CANDIDATE، WAIT/MONITOR، NO_TRADE، NOT_VALIDATED یا DATA_INVALID.
6. ثبت دلایل اقدام نکردن و سنجش کیفیت abstention در برابر اجبار به معامله.
7. اثبات یا رد فرضیه با آزمون خارج از نمونه مستقل، نه با روایت یا بک‌تست انتخاب‌شده.

**معیار موفقیت:** سیستم باید بتواند به‌درستی بگوید «نمی‌دانم»، «داده کافی نیست» یا «این معامله از نظر اقتصادی قابل اجرا نیست». تعداد سیگنال معیار موفقیت نیست.

## 2) مشکلات و شکاف‌های قطعیِ مستندشده

### F-01 | اعتبار معاملاتی Futures اثبات نشده است — بحرانی
- موتور `futures_signal_engine.py` از OHLCV بسته‌شده و چند شاخص ساده (EMA20/50، مومنتوم، RSI، ATR) سناریوی LONG/SHORT می‌سازد.
- امتیاز جهت‌دار heuristic است، probability یا confidence کالیبره‌شده نیست.
- خروجی طبق قرارداد WATCH_ONLY و تصمیم همیشه NO_TRADE است.
- پس این موتور در وضعیت فعلی «معامله معتبر Futures» تولید نمی‌کند؛ فقط سناریوی پژوهشی می‌سازد.
- **اقدام:** تا تکمیل موتور مشتقات، اجرا و اعتبارسنجی اختصاصی، هر نمایش Entry/SL/TP باید صریحاً غیرقابل‌معامله بماند.

### F-02 | بک‌تست تاریخی Futures معادل قرارداد واقعی نیست — بحرانی
مستندات معماری و README تصریح می‌کنند که بک‌تست قدیمی از اهرم/Notional مصنوعی استفاده می‌کند و مسیر تاریخی Funding، Margin Call و Liquidation را شبیه‌سازی نمی‌کند. در نتیجه بازده و Drawdown آن برای ادعای قابلیت معامله در Futures کافی نیست.

**اقدام:** موتور جداگانه‌ی قراردادمحور؛ نه افزودن یک ضریب اهرم به بک‌تست Spot.

### F-03 | قیمت Mark/Index و قیمت معامله یکسان نیستند — بحرانی
قیمت کندل معامله، Mark Price و Index Price کاربردهای متفاوت دارند. قیمت اجرای فرضی، PnL، trigger استاپ و Liquidation نباید بدون قرارداد زمانی/قیمتی مشخص از یک سری قیمت ساخته شوند.

**اقدام:** ثبت جداگانه‌ی Trade/Last، Bid/Ask، Mark، Index و زمان هر مشاهده؛ تعریف صریح اینکه هر رویداد بر اساس کدام قیمت trigger می‌شود.

### F-04 | Liquidation فعلی فقط تخمین عمومی است — بحرانی
`margin_engine.py` یک برآورد Isolated خطی ارائه می‌کند؛ خود مستندات محدودیت tierهای نگهداری، Cross Margin و قوانین واقعی صرافی را اعلام می‌کنند. این خروجی جایگزین موتور لیکوییدیشن صرافی نیست.

**اقدام:** metadata زمان‌مند tier/bracket، maintenance amount/rate، liquidation fee، wallet mode، سایر پوزیشن‌ها و قواعد mark-price trigger؛ در غیر این صورت وضعیت باید UNVERIFIED باشد.

### F-05 | Funding باید رویداد نقدی زمان‌مند باشد — بالا
داشتن نرخ Funding جاری یا تاریخی به‌تنهایی کافی نیست. شبیه‌سازی باید زمان settlement، نرخ قابل‌اعمال در همان زمان، جهت پوزیشن، Notional و تغییر اندازه پوزیشن را لحاظ کند. نرخ ثابت یا صفر پیش‌فرض می‌تواند نتیجه را منحرف کند.

**اقدام:** Funding event ledger با زمان مؤثر، زمان دریافت، نرخ، منبع و محاسبه cashflow روی اندازه واقعی پوزیشن.

### F-06 | داده OI یک مشاهده‌ی موقعیت‌گیری کامل نیست — بالا
Open Interest تغییر تعداد قراردادهای باز را نشان می‌دهد، نه به‌تنهایی اینکه بازار Long یا Short شده است. واحد قرارداد/دارایی/Notional و فاصله زمانی baseline باید حفظ شود.

**اقدام:** نمایش تغییر OI در واحد اصلی و quote notional، هم‌زمان با قیمت، Funding و حجم؛ ممنوعیت تفسیر مستقل آن به‌عنوان جهت.

### F-07 | Snapshot دفتر سفارش و نمونه محدود معاملات، جریان کامل بازار نیست — بحرانی
در endpoint داشبورد Futures، Order Book یک REST snapshot و معاملات اخیر نمونه‌ای محدود است. این دو معادل depth stream همگام‌شده و Tick-by-Tick کامل نیستند و در موتور جهت‌دار نیز مصرف نمی‌شوند.

**اقدام:** فقط داده stream بازسازی‌شده با sequence معتبر، coverage و replay audit می‌تواند وارد featureهای پژوهشی شود.

### F-08 | خام‌داده Futures با Forecast فعلی یکپارچه نشده — بحرانی
پروتکل Holdout صریحاً می‌گوید collector مستقل Spot و USD-M Futures را ذخیره می‌کند، اما این به معنی مصرف خودکار آن توسط Forecast یا امتیازدهی آن در ledger نیست. اتصال خودکار microstructure فعلی در مسیر مشخص‌شده Spot BTCUSDT است و حتی آنجا PMI فعلی بردار microstructure را به‌عنوان ورودی مدل مصرف نمی‌کند.

**اقدام:** adapter نسخه‌دار PIT برای USD-M، اتصال manifest و feature hash به forecast، و ثبت watermark که از cutoff تصمیم جلوتر نرود. تا آن زمان ادعای مدل OHLCV+Tick+L2 ممنوع.

### F-09 | اجرای سفارش و صف سفارش واقعی شبیه‌سازی نشده — بحرانی
L2 market replay برای تخمین sweep و نقدشوندگی مفید است، اما fill واقعی را اثبات نمی‌کند. صف سفارش limit، اولویت زمانی، cancel/replace، latency، partial fills، adverse selection و queue depletion هنوز باید جداگانه مدل شوند.

**اقدام:** ابتدا marketable-order replay، سپس queue-aware passive replay با فرض‌های محافظه‌کارانه و سطح اعتبار مجزا.

### F-10 | ریسک قرارداد و مشخصات صرافی باید زمان‌مند باشد — بحرانی
tick size، step size، min notional، contract multiplier، max leverage، margin brackets، trading status و فیلترها ممکن است تغییر کنند. اعتبارسنجی rules از metadata نرمال‌شده به‌تنهایی کافی نیست مگر source، timestamp و نسخه قرارداد ثبت شود.

**اقدام:** Exchange Contract Registry با snapshotهای versioned و effective time؛ هر plan به نسخه دقیق قرارداد bind شود.

### F-11 | Prospective Holdout هنوز شروع نشده — بحرانی
مستندات می‌گویند هنوز داده آینده جمع‌آوری نشده است. همچنین مسیر فعلی Holdout در API برای Spot تعریف شده و Futures در Runbook فعلی پشتیبانی نمی‌شود.

**اقدام:** برای Futures cohort مستقل؛ شروع ساعت 42 روزه ممنوع تا collector، PIT adapter، forecast ledger، resolver، هزینه‌ها و بازیابی عملیاتی end-to-end تأیید شوند.

### F-12 | 200 نمونه، جایگزین توان آماری نیست — بالا
عدد 200 یک حد عملیاتی اولیه است، نه تضمین power. در افق‌های بلند یا داده وابسته، Effective Sample Size ممکن است بسیار کمتر باشد.

**اقدام:** پیش از ثبت cohort، power/MDE و نرخ نمونه‌گیری با شبیه‌سازی وابستگی زمانی برآورد شود؛ اگر 42 روز کافی نیست، پنجره تمدید شود.

### F-13 | ادعای Edge هنوز وجود ندارد — بحرانی
نتایج منفی قبلی برای استراتژی‌های آزمایش‌شده USDT/IRR در 1h فقط به همان دامنه مربوط‌اند؛ نه حکم کلی درباره تمام بازارها و افق‌ها. در عین حال هیچ ادعای Edge برای Futures در این ممیزی تأیید نشده است.

**اقدام:** ثبت فرضیه‌های محدود و preregistered؛ مقایسه با baseline و تمام هزینه‌ها؛ گزارش CI و multiplicity؛ نگه‌داشتن NO_EDGE/UNVERIFIED به‌عنوان نتیجه معتبر.

## 3) ریسک‌های عمومی که باید ممیزی شوند

این موارد در وضعیت فعلی «نیازمند ممیزی انتها‌به‌انتها» هستند، نه باگ قطعی:

- هم‌ترازی timezone، event time، transaction time، receive time و clock drift.
- leakage از کندل باز، داده دیررس، اصلاح‌شده یا timestamp آینده.
- sequence gap، reconnect، snapshot bridge، buffer overflow و بازیابی پس از crash.
- تفاوت symbol، contract type، quote asset، settlement asset و واحد حجم.
- اختلاف کندل‌سازی بین providerها و مرز زمانی کندل.
- hash binding از raw frame تا feature، forecast، outcome و گزارش نهایی.
- رفتار در disk-full، permission loss، clock rollback، duplicate event و partition corruption.
- پوشش آزمون برای هر state transition، نه فقط مسیر happy path.
- وابستگی داده به یک venue و ریسک outage/maintenance.
- هزینه واقعی حساب کاربر در برابر fee schedule عمومی.
- market impact، latency و ظرفیت در اندازه‌های مختلف سفارش.
- هم‌بستگی سیگنال‌ها و ریسک پرتفوی در چند نماد.
- کالیبراسیون احتمالات، drift، regime transition و کیفیت NO_TRADE.
- حسابداری تمام آزمایش‌ها و جلوگیری از انتخاب بهترین نتیجه از میان آزمون‌های متعدد.
- امنیت API، token، مسیرهای LAN و جداسازی داده عملیاتی از checkout کد.
- تجربه کاربری: تفکیک واضح «مشاهده بازار»، «پیش‌بینی»، «سناریو»، «شبیه‌سازی» و «تصمیم مجاز».

## 4) اهداف محصول و معیارهای پذیرش

| حوزه | هدف | شرط پذیرش |
|---|---|---|
| Data Truth | مشاهده‌ی قابل بازپخش و قابل ممیزی | PIT، sequence، provenance، hash و coverage |
| Futures Truth | بازنمایی قرارداد واقعی | قیمت‌ها، Funding، margin tiers و قواعد trigger زمان‌مند |
| Forecast | توزیع آینده، نه یک عدد قطعی | baseline، calibration و uncertainty |
| Microstructure | استفاده علّی از Tick و L2 | cutoff معتبر، replay و feature binding |
| Execution | شبیه‌سازی محافظه‌کارانه | spread، fee، impact، latency، fills و queue assumptions |
| Risk | سنجش زیان واقعی قرارداد | liquidation، funding cashflow و portfolio exposure |
| Evidence | کشف/رد Edge | preregistration، OOS، CI، multiplicity و sample adequacy |
| Decision | تصمیم قابل توضیح | ACT/PAPER، WAIT، NO_TRADE، NOT_VALIDATED، DATA_INVALID |
| Operations | سرویس پایدار | restart، monitoring، backup، recovery و alerting |
| UX | جلوگیری از برداشت غلط | نشان دادن سطح اعتبار هر خروجی و دلایل عدم معامله |

## 5) اولویت‌بندی اجرا

1. **P0: Futures Truth Gate** — جلوگیری از نمایش هر سناریوی Futures به‌عنوان معامله معتبر.
2. **P0: Contract & Price Identity** — تعریف هویت قرارداد و جداسازی Last/Mark/Index/Bid/Ask.
3. **P0: Futures PIT Evidence Adapter** — اتصال خام‌داده USD-M به feature/forecast/evaluation.
4. **P0: Exchange-faithful Risk & Funding** — tierها، funding settlement و liquidation semantics.
5. **P1: Execution Reality Engine** — replay بازارپذیر و سپس صف limit.
6. **P1: Forecast Lab** — پیش‌بینی چندافقی، baseline و uncertainty decomposition.
7. **P1: Regime & Cross-Asset Intelligence** — رژیم‌ها و تعامل بازارها، با آزمون جداگانه.
8. **P1: Prospective Futures Holdout** — فقط بعد از عبور از gates بالا.
9. **P2: Portfolio Intelligence** — exposure، correlation، tail-risk و capital allocation.
10. **P2: UX/Observability** — توضیح evidence و وضعیت سلامت داده در داشبورد.

## 6) قوانین غیرقابل‌مذاکره

- هیچ سناریوی Futures تا قبل از اثبات، «سیگنال قابل معامله» نامیده نشود.
- هیچ مقدار Funding، slippage، impact یا liquidation ناشناخته به صفر تبدیل نشود.
- Spot، USD-M linear، COIN-M inverse و dated futures هرگز با یک فرمول واحد و بدون هویت قراردادی مخلوط نشوند.
- نبود داده به معنی صفر بودن آن نیست.
- داده ناقص/قدیمی/ناسازگار باید fail-closed شود.
- هیچ تغییر مدل یا feature در cohort در حال اجرا مجاز نیست.
- آزمون آینده برای tuning مصرف نمی‌شود.
- سبز بودن CI فقط کیفیت نرم‌افزار را می‌سنجد، نه Edge را.
- 42 روز تقویمی به‌تنهایی معیار کفایت آماری نیست.

## 7) وضعیت اجرایی در زمان نگارش

- مبنای کد: commit فوق.
- Futures Directional Engine: سناریوساز پژوهشی OHLCV، WATCH_ONLY.
- Futures historical backtest: برای اثبات اقتصاد قرارداد واقعی کافی نیست.
- Binance raw collector: Spot و USD-M را مستقل capture می‌کند؛ اتصال کامل Futures به forecast اثبات نشده.
- Prospective API/Holdout: مسیر مستند فعلی Spot محور است.
- 42-day clock: شروع نشده.
- Live order execution: خارج از دامنه.
- سودآوری/Edge Futures: اثبات نشده.
