# ChartAnalyzer V5

نسخه V5 بر پایه کد موجود ساخته شده و موتورهای قبلی را حفظ می‌کند. قابلیت جدید PMI برای تحلیل رژیم و سناریوهای احتمالاتی به پروژه افزوده شده است.

## شروع سریع
```bash
python -m pip install -r requirements.txt
python app.py
```

برای مشاهده PMI:
```text
http://127.0.0.1:8765/api/pmi?symbol=BTCIRR&tf=1h&horizons=1,4,12,24
```

`horizons` تعداد کندل است. خروجی شامل جهت‌گیری امتیازی، رژیم، شکست محدوده و quantileهای سناریو است. این امتیاز احتمال نیست.

## آزمون
```bash
python -m pytest -q
```

جزئیات تغییرات، مراحل V4.0 تا V4.9، محدودیت‌ها و روش اجرا در `RELEASE_NOTES_V5_FA.md` آمده است.
