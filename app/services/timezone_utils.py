"""
Centralized timezone utilities.

Eliminates the 50+ files that independently define IST = timezone(timedelta(hours=5, minutes=30)).
"""

from datetime import date, datetime, timezone, timedelta


UTC = timezone.utc
IST = timezone(timedelta(hours=5, minutes=30))


def now_utc() -> datetime:
    """Return the current UTC datetime (timezone-aware)."""
    return datetime.now(UTC)


def now_ist() -> datetime:
    """Return the current IST datetime (timezone-aware)."""
    return datetime.now(IST)


def today_ist() -> date:
    """Return today's date in IST."""
    return now_ist().date()


def to_ist(dt: datetime) -> datetime:
    """Convert any datetime to IST. Naive datetimes are assumed to be UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(IST)


def ensure_aware(dt: datetime, assume_tz: timezone = IST) -> datetime:
    """Make a naive datetime timezone-aware, defaulting to *assume_tz*."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=assume_tz)
    return dt
