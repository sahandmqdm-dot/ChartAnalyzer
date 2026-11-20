# استقرار آزمون آینده‌نگر روی VPS لینوکس

## معماری امن پیشنهادی

هر دو سرویس روی **یک VPS** اجرا می‌شوند:

- `chartanalyzer.service`: API و موتور PMI، فقط روی `127.0.0.1:8765`.
- `chartanalyzer-prospective-collector.service`: Collector که فقط از loopback به API درخواست می‌زند.
- دیتابیس هر Cohort در `/var/lib/chartanalyzer/prospective_holdouts`.
- وضعیت و heartbeat در `/var/lib/chartanalyzer/collector-state.json`.
- پیکربندی Cohort خارج از مخزن در `/etc/chartanalyzer/prospective-cohort.json`.

**پورت 8765 را در Firewall عمومی باز نکنید.** برای مشاهده داشبورد از تونل SSH استفاده کنید:

```powershell
ssh -L 8765:127.0.0.1:8765 USER@VPS_HOST
```

سپس در مرورگر کامپیوتر خود `http://127.0.0.1:8765` را باز کنید. API به اینترنت عمومی ارائه نمی‌شود و نیازی به Nginx یا TLS عمومی برای Collector ندارد.

## 1. مشخصات سرور

برای Cohort پیشنهادی BTC Spot، یک VPS معمولی کافی است:

- Ubuntu Server 24.04 LTS یا توزیع پشتیبانی‌شده معادل
- 2 vCPU
- 4 GB RAM
- حداقل 30 GB SSD پایدار
- اتصال اینترنت پایدار
- منطقه‌ای که دسترسی قانونی و قابل اتکا به Data Provider انتخابی داشته باشد

GPU لازم نیست؛ مدل PMI فعلی بر پایه محاسبات آماری/Bootstrap است. این مشخصات نقطه شروع عملیاتی‌اند، نه Benchmark ظرفیت‌سنجی‌شده.

## 2. ایجاد کاربر و مسیرها

با کاربر دارای sudo:

```bash
sudo useradd --system --create-home --home-dir /opt/chartanalyzer --shell /usr/sbin/nologin chartanalyzer
sudo install -d -o chartanalyzer -g chartanalyzer -m 0750 /opt/chartanalyzer
sudo install -d -o chartanalyzer -g chartanalyzer -m 0700 /var/lib/chartanalyzer
sudo install -d -o root -g chartanalyzer -m 0750 /etc/chartanalyzer
```

کد Release تثبیت‌شده را در `/opt/chartanalyzer` مستقر کنید. برای آزمون معتبر، از branch آزمایشی یا working tree ویرایش‌شده استفاده نکنید. SHA دقیق Release را در `/etc/chartanalyzer/release.env` ثبت کنید:

```bash
sudo sh -c 'printf "CHARTANALYZER_RELEASE_SHA=%s\\n" "REPLACE_WITH_FROZEN_COMMIT_SHA" > /etc/chartanalyzer/release.env'
sudo chown root:chartanalyzer /etc/chartanalyzer/release.env
sudo chmod 0640 /etc/chartanalyzer/release.env
```

## 3. نصب Python و وابستگی‌ها

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip git
sudo -u chartanalyzer python3 -m venv /opt/chartanalyzer/.venv
sudo -u chartanalyzer /opt/chartanalyzer/.venv/bin/pip install --upgrade pip
sudo -u chartanalyzer /opt/chartanalyzer/.venv/bin/pip install -r /opt/chartanalyzer/requirements.txt
```

اگر منبع Cohort از CCXT استفاده می‌کند، وابستگی اختیاری را نیز نصب کنید:

```bash
sudo -u chartanalyzer /opt/chartanalyzer/.venv/bin/pip install ccxt
```

برای منبع Ramzinex، CCXT لازم نیست. دسترسی واقعی VPS به منبع را پیش از ثبت Cohort با `/api/data-integrity` بررسی کنید.

## 4. تنظیمات API و دسترسی

فایل `config.json` را در مسیر برنامه قرار دهید و بازار Backtest را روی Spot تنظیم کنید. برای سرویس تک‌ماشینه، host را روی loopback نگه دارید. گزینه `--lan` را اجرا نکنید و API را روی `0.0.0.0` منتشر نکنید.

پیش از راه‌اندازی سرویس، مطمئن شوید هیچ سرویس دیگری روی پورت 8765 گوش نمی‌دهد. فایل‌های config و داده باید فقط برای کاربر سرویس قابل خواندن/نوشتن باشند.

## 5. نصب Unitها

از مخزن:

```bash
sudo install -o root -g root -m 0644 deploy/systemd/chartanalyzer.service /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/systemd/chartanalyzer-prospective-collector.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now chartanalyzer.service
sudo systemctl status chartanalyzer.service --no-pager
```

قبل از شروع Collector، API را روی همان VPS تست کنید:

```bash
curl --fail http://127.0.0.1:8765/api/data-integrity?source=ramzinex\&symbol=BTCUSDT\&tf=1h\&n=200
```

این فقط نمونه است. اگر provider یا نماد متفاوت است، پارامترها را مطابق همان منبع تنظیم کنید. تا وقتی داده معتبر و تازه دریافت نمی‌شود، Cohort را ثبت نکنید.

## 6. ثبت Cohort و پیکربندی Collector

ابتدا یک Cohort با درخواست ثبت مستندشده در `docs/PROSPECTIVE_HOLDOUT_RUNBOOK_FA.md` ایجاد کنید. همان payload را با همان مقادیر و زمان‌ها نگه دارید. Collector خودش Cohort ایجاد نمی‌کند و هیچ پارامتری را تغییر نمی‌دهد.

پس از موفقیت ثبت، فایل زیر را بسازید:

`/etc/chartanalyzer/prospective-cohort.json`

ساختار نمونه برای BTC/USDT با CCXT:

```json
{
  "api_base_url": "http://127.0.0.1:8765",
  "poll_seconds": 300,
  "request_timeout_seconds": 45,
  "state_file": "/var/lib/chartanalyzer/collector-state.json",
  "cohort_payload": {
    "cohort_id": "BTCUSDT-SPOT-1H-H4-PROSPECTIVE-01",
    "source": "ccxt",
    "exchange": "binance",
    "symbol": "BTC/USDT",
    "tf": "1h",
    "horizon_bars": 4,
    "paths": 3000,
    "n": 1200
  }
}
```

این مقادیر باید دقیقاً با payload ثبت Cohort مطابقت داشته باشند. برای Ramzinex، `source` را `ramzinex` قرار دهید و فیلد `exchange` را حذف کنید. مقدار `symbol` باید همان نمادی باشد که در زمان ثبت استفاده شد.

دسترسی فایل را محدود کنید:

```bash
sudo chown root:chartanalyzer /etc/chartanalyzer/prospective-cohort.json
sudo chmod 0640 /etc/chartanalyzer/prospective-cohort.json
```

## 7. تست یک Tick بدون شروع سرویس دائمی

```bash
sudo -u chartanalyzer /opt/chartanalyzer/.venv/bin/python /opt/chartanalyzer/prospective_collector.py --config /etc/chartanalyzer/prospective-cohort.json --once
sudo cat /var/lib/chartanalyzer/collector-state.json
```

اگر وضعیت Cohort ثبت‌شده نیست، fingerprint/config با هم سازگار نیستند، Integrity برابر PASS نیست یا provider خطا دارد، Collector با خطا خارج می‌شود. تا رفع علت، سرویس دائمی را فعال نکنید.

## 8. فعال‌سازی و پایش

```bash
sudo systemctl enable --now chartanalyzer-prospective-collector.service
sudo systemctl status chartanalyzer-prospective-collector.service --no-pager
sudo journalctl -u chartanalyzer.service -f
sudo journalctl -u chartanalyzer-prospective-collector.service -f
```

Collector هر 5 دقیقه status و tick را اجرا می‌کند. خطاها با backoff افزایشی تکرار می‌شوند. پس از Restart سیستم، systemd هر دو سرویس را بالا می‌آورد. قفل سیستم‌عاملی مانع اجرای هم‌زمان دو Collector می‌شود. فایل heartbeat به‌صورت atomic نوشته می‌شود و آخرین موفقیت، تعداد خطاهای متوالی و خلاصه tick را ثبت می‌کند.

برای پایش سریع:

```bash
sudo systemctl is-active chartanalyzer.service chartanalyzer-prospective-collector.service
sudo cat /var/lib/chartanalyzer/collector-state.json
```

وضعیت Ledger و شمارنده‌ها را از endpoint status بخوانید؛ Collector هیچ‌گاه endpoint ارزیابی نهایی را فراخوانی نمی‌کند.

## 9. پشتیبان‌گیری و پایان دوره

- هر روز از `/var/lib/chartanalyzer/prospective_holdouts/` و فایل heartbeat نسخه پشتیبان تهیه کنید.
- هنگام پشتیبان‌گیری، SQLite را با روش سازگار با SQLite کپی کنید یا ابتدا از `sqlite3 .backup` استفاده کنید؛ کپی خام دیتابیس فعال به‌همراه WAL را به‌عنوان Backup معتبر فرض نکنید.
- Backup را خارج از VPS نگه دارید و دسترسی آن را محدود کنید.
- هشدار قطعی اینترنت، توقف سرویس و عدم به‌روزرسانی heartbeat را مانیتور کنید.
- در پایان بازه، Collector باید همچنان اجرا شود تا پیش‌بینی‌های سررسیدشده resolve شوند. پس از پایان collection window، status باید به AWAITING_RESOLUTION برسد و unresolved_count به صفر برسد.
- Integrity باید PASS باشد. سپس اپراتور به‌صورت دستی endpoint evaluate را **فقط یک بار** فراخوانی کند.
- گزارش نهایی را همراه SHA Release، تنظیمات ثبت‌شده، فایل دیتابیس و لاگ‌های عملیاتی آرشیو کنید.

## 10. نکات اعتبارسنجی

این استقرار به معنای شروع خودکار 42 روز نیست. شمارش فقط بعد از ثبت موفق Cohort با زمان شروع آینده و ثبت اولین پیش‌بینی معتبر آغاز می‌شود. از آن تاریخ، هیچ کد، وابستگی، تنظیمات مدل، provider، symbol، timeframe، horizon یا history window را تغییر ندهید.

این سیستم پیش‌بینی توزیعی را ارزیابی می‌کند؛ سودآوری استراتژی یا کیفیت اجرای سفارش را اثبات نمی‌کند. API عمداً روی loopback می‌ماند و دسترسی مدیریتی از SSH tunnel انجام می‌شود.


## پشتیبان‌گیری خودکار روزانه

برای محافظت از داده‌های آزمون 42 روزه، مخزن شامل اسکریپت Backup سازگار با SQLite و Timer روزانه systemd است. این روش از SQLite Online Backup API استفاده می‌کند و با WAL سازگار است؛ فایل دیتابیس فعال را به‌صورت خام کپی نمی‌کند.

پس از نصب سرویس API و ایجاد مسیر Backup:

~~~bash
sudo install -d -o chartanalyzer -g chartanalyzer -m 0700 /var/backups/chartanalyzer/prospective_holdouts
sudo install -o root -g root -m 0644 deploy/systemd/chartanalyzer-prospective-backup.service /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/systemd/chartanalyzer-prospective-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now chartanalyzer-prospective-backup.timer
~~~

اجرای آزمایشی دستی:

~~~bash
sudo systemctl start chartanalyzer-prospective-backup.service
sudo journalctl -u chartanalyzer-prospective-backup.service -n 50 --no-pager
sudo ls -lh /var/backups/chartanalyzer/prospective_holdouts
~~~

هر Backup با `PRAGMA integrity_check` بررسی می‌شود و فایل Manifest شامل SHA-256، زمان ایجاد و اندازه دارد. نگهداری پیش‌فرض 30 روز است. یک نسخه خارج از VPS نیز نگه دارید؛ Backup روی همان دیسک جایگزین Disaster Recovery نیست.


## جمع‌آوری مستقل Raw Tick و Order Book از Binance

این سرویس از Prospective PMI Collector جداست. آن را برای آرشیو داده خام Spot و USD-M Futures اجرا کنید؛ فعال‌سازی آن به‌تنهایی به معنی ورود Microstructure به مدل PMI یا شروع Holdout نیست.

ابتدا مسیر داده را ایجاد کنید:

~~~bash
sudo install -d -o chartanalyzer -g chartanalyzer -m 0700 /var/lib/chartanalyzer/binance_raw
sudo install -o root -g root -m 0644 deploy/systemd/chartanalyzer-binance-raw-collector.service /etc/systemd/system/
sudo systemctl daemon-reload
~~~

سپس سرویس را فعال کنید:

~~~bash
sudo systemctl enable --now chartanalyzer-binance-raw-collector.service
sudo systemctl status chartanalyzer-binance-raw-collector.service --no-pager
sudo journalctl -u chartanalyzer-binance-raw-collector.service -f
~~~

Collector به‌طور هم‌زمان BTCUSDT Spot و USD-M Futures را می‌گیرد. رویدادهای خام WebSocket پیش از JSON parsing در `raw_frames` ذخیره می‌شوند؛ رویدادهای تایپ‌شده، Snapshot و وضعیت همگام‌سازی Order Book نیز در پارتیشن‌های جداگانه نوشته می‌شوند. متن خام با UTF-8 و پیام باینری با Base64 حفظ می‌شود.

مسیر داده شامل پارتیشن‌های روزانه JSONL و Hash Chain است. پس از توقف تمیز، فایل‌ها flush و fsync می‌شوند. در حال حاضر سیاست fsync پیش‌فرض هر 100 رویداد است؛ بنابراین این طراحی در برابر قطع ناگهانی برق تضمین صفر-اتلاف نمی‌دهد. برای شروع آزمون رسمی باید نرخ واقعی داده، ظرفیت دیسک، تأخیر fsync، شمارنده رویدادهای ازدست‌رفته و بازیابی پس از قطع ناگهانی جداگانه اندازه‌گیری شوند.

اعتبار زنجیره هر پارتیشن را می‌توان با تابع `HashChainJSONLStore.verify_file` در Python بررسی کرد. قبل از استفاده پژوهشی، تمام فایل‌ها را Verify کنید و Manifest روزانه شامل تعداد رویداد، اولین/آخرین timestamp، آخرین hash، تعداد gapها و تعداد خطاهای ذخیره‌سازی را خارج از مسیر داده اصلی آرشیو کنید.
