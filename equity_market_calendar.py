"""DST-aware Nasdaq/NYSE session calendar.

This module provides a deterministic baseline calendar for US cash equities.
It does not report live halts, unscheduled closures or broker-specific sessions.
For production, exchange notices must override this calendar.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo


NEW_YORK = ZoneInfo("America/New_York")


class SessionPhase(str, Enum):
    CLOSED = "closed"
    PRE_MARKET = "pre_market"
    REGULAR = "regular"
    AFTER_HOURS = "after_hours"


@dataclass(frozen=True, slots=True)
class MarketSession:
    venue: str
    session_date: date
    phase: SessionPhase
    is_trading_day: bool
    pre_market_open: datetime | None
    regular_open: datetime | None
    regular_close: datetime | None
    after_hours_close: datetime | None
    reason: str | None = None


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        last = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _easter_sunday(year: int) -> date:
    """Gregorian Easter computus."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def market_holidays(year: int) -> frozenset[date]:
    """Baseline full-day US equity holidays for Nasdaq and NYSE."""
    days = {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),       # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),       # Washington's Birthday
        _easter_sunday(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),         # Memorial Day
        _observed(date(year, 7, 4)),       # Independence Day
        _nth_weekday(year, 9, 0, 1),       # Labor Day
        _nth_weekday(year, 11, 3, 4),      # Thanksgiving
        _observed(date(year, 12, 25)),     # Christmas
    }
    # Juneteenth became a federal market holiday in 2022.
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))
    # New Year's Day can be observed on December 31 of the prior year.
    days.add(_observed(date(year + 1, 1, 1)))
    return frozenset(d for d in days if d.year == year)


def early_close_dates(year: int) -> frozenset[date]:
    """Common scheduled 13:00 ET early closes; exceptional notices are external."""
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    candidates = {
        thanksgiving + timedelta(days=1),
        date(year, 7, 3),
        date(year, 12, 24),
    }
    holidays = market_holidays(year)
    return frozenset(
        d for d in candidates
        if d.weekday() < 5 and d not in holidays
    )


class USMarketCalendar:
    """Session classifier for Nasdaq/NYSE cash-equity regular and extended hours."""

    def __init__(self, venue: str = "NASDAQ", *, timezone_name: str = "America/New_York") -> None:
        venue = str(venue).strip().upper()
        if venue not in {"NASDAQ", "NYSE"}:
            raise ValueError("venue must be NASDAQ or NYSE")
        self.venue = venue
        try:
            self.timezone = ZoneInfo(timezone_name)
        except Exception as exc:
            raise ValueError("timezone_name must be a valid IANA timezone") from exc

    def is_trading_day(self, day: date) -> bool:
        if not isinstance(day, date) or isinstance(day, datetime):
            raise TypeError("day must be a date")
        return day.weekday() < 5 and day not in market_holidays(day.year)

    def session_for(self, timestamp: datetime) -> MarketSession:
        if not isinstance(timestamp, datetime) or timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        local = timestamp.astimezone(self.timezone)
        day = local.date()
        if not self.is_trading_day(day):
            return MarketSession(self.venue, day, SessionPhase.CLOSED, False, None, None, None, None, "weekend_or_holiday")

        close_time = time(13, 0) if day in early_close_dates(day.year) else time(16, 0)
        pre = datetime.combine(day, time(4, 0), self.timezone)
        regular_open = datetime.combine(day, time(9, 30), self.timezone)
        regular_close = datetime.combine(day, close_time, self.timezone)
        after_close = datetime.combine(day, time(20, 0), self.timezone)

        if local < pre or local >= after_close:
            phase = SessionPhase.CLOSED
        elif local < regular_open:
            phase = SessionPhase.PRE_MARKET
        elif local < regular_close:
            phase = SessionPhase.REGULAR
        else:
            phase = SessionPhase.AFTER_HOURS

        return MarketSession(
            self.venue, day, phase, True, pre, regular_open, regular_close, after_close,
            "scheduled_early_close" if close_time == time(13, 0) else None,
        )


__all__ = ["SessionPhase", "MarketSession", "USMarketCalendar", "market_holidays", "early_close_dates"]
