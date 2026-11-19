from datetime import datetime, date, timezone
import pytest
from equity_market_calendar import SessionPhase, USMarketCalendar, early_close_dates, market_holidays


def test_regular_and_extended_sessions_are_dst_aware():
    cal = USMarketCalendar("NASDAQ")
    # 2026-01-05 is standard time: 14:30 UTC = 09:30 ET.
    regular = cal.session_for(datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc))
    assert regular.phase is SessionPhase.REGULAR
    assert regular.regular_open.hour == 9 and regular.regular_open.utcoffset().total_seconds() == -18000
    # 2026-07-06 is daylight time: 13:30 UTC = 09:30 ET.
    summer = cal.session_for(datetime(2026, 7, 6, 13, 30, tzinfo=timezone.utc))
    assert summer.phase is SessionPhase.REGULAR
    assert summer.regular_open.utcoffset().total_seconds() == -14400


@pytest.mark.parametrize("utc,phase", [
    (datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc), SessionPhase.PRE_MARKET),
    (datetime(2026, 1, 5, 22, 0, tzinfo=timezone.utc), SessionPhase.AFTER_HOURS),
    (datetime(2026, 1, 5, 23, 0, tzinfo=timezone.utc), SessionPhase.CLOSED),
])
def test_session_phases(utc, phase):
    assert USMarketCalendar().session_for(utc).phase is phase


def test_weekends_and_major_holidays_closed():
    cal = USMarketCalendar("NYSE")
    assert not cal.is_trading_day(date(2026, 1, 1))
    assert not cal.is_trading_day(date(2026, 4, 3))  # Good Friday
    assert not cal.is_trading_day(date(2026, 11, 26))  # Thanksgiving
    assert not cal.is_trading_day(date(2026, 7, 4))
    assert not cal.is_trading_day(date(2026, 6, 19))
    assert not cal.is_trading_day(date(2026, 1, 3))


def test_early_close_after_thanksgiving():
    assert date(2026, 11, 27) in early_close_dates(2026)
    session = USMarketCalendar().session_for(datetime(2026, 11, 27, 18, 30, tzinfo=timezone.utc))
    assert session.phase is SessionPhase.AFTER_HOURS
    assert session.regular_close.hour == 13


def test_new_year_observed_on_prior_year_december_31():
    assert date(2021, 12, 31) in market_holidays(2021)


def test_invalid_venue_and_naive_timestamp_rejected():
    with pytest.raises(ValueError):
        USMarketCalendar("OTC")
    with pytest.raises(ValueError, match="timezone-aware"):
        USMarketCalendar().session_for(datetime(2026, 1, 5, 10, 0))


def test_market_holidays_do_not_mark_weekends_as_trading_days():
    cal = USMarketCalendar()
    assert not cal.is_trading_day(date(2026, 1, 4))
