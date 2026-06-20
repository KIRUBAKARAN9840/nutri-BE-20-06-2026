"""
Date-range and calendar utilities.

Eliminates duplicated week_start/week_end and month/day range calculations
scattered across admin dashboard and assignment files.
"""

from datetime import date, datetime, timedelta
from typing import Tuple


def week_range(reference: date = None) -> Tuple[date, date]:
    """
    Return (Monday, Sunday) of the week containing *reference*.

    >>> week_range(date(2026, 3, 11))  # Wednesday
    (date(2026, 3, 9), date(2026, 3, 15))
    """
    if reference is None:
        reference = date.today()
    week_start = reference - timedelta(days=reference.weekday())  # Monday
    week_end = week_start + timedelta(days=6)                     # Sunday
    return week_start, week_end


def month_range(reference: date = None) -> Tuple[date, date]:
    """
    Return (first day, last day) of the month containing *reference*.
    """
    if reference is None:
        reference = date.today()
    first = reference.replace(day=1)
    # Jump to next month's 1st, then back one day
    if reference.month == 12:
        last = reference.replace(year=reference.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        last = reference.replace(month=reference.month + 1, day=1) - timedelta(days=1)
    return first, last


def days_ago(n: int, reference: date = None) -> date:
    """Return the date *n* days before *reference* (default: today)."""
    if reference is None:
        reference = date.today()
    return reference - timedelta(days=n)


def start_of_day(d: date = None) -> datetime:
    """Return midnight (00:00:00) for the given date."""
    if d is None:
        d = date.today()
    return datetime.combine(d, datetime.min.time())


def end_of_day(d: date = None) -> datetime:
    """Return 23:59:59.999999 for the given date."""
    if d is None:
        d = date.today()
    return datetime.combine(d, datetime.max.time())
