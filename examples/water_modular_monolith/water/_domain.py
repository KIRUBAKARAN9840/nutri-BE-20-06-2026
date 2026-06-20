

from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo

from .schemas import DayStreak


GLASS_LITRES = 0.25  
IST = ZoneInfo("Asia/Kolkata")
STREAK_DAYS = 7


def now_ist() -> datetime:
    return datetime.now(IST)


def add_one_glass(current_litres: float) -> float:
    return current_litres + GLASS_LITRES


def compute_next_reminder_time(
    *,
    start_time: time,
    end_time: time,
    water_timing: float,
    now: datetime,
) -> datetime:

    naive_now = now.replace(tzinfo=None)
    if naive_now.minute < 30:
        boundary = naive_now.replace(minute=30, second=0, microsecond=0)
    else:
        boundary = naive_now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    end_dt = datetime.combine(naive_now.date(), end_time)
    if boundary > end_dt:
        boundary = end_dt

    if water_timing < 1:
        return boundary

    next_dt = boundary if boundary.minute == 0 else boundary + timedelta(minutes=30)
    return min(next_dt, end_dt)


def is_outside_window(start: time, end: time, current: time) -> bool:
    return not (start <= current <= end)


def format_last_drink_time(
    last_time: Optional[datetime], now: datetime
) -> Optional[str]:

    if not last_time:
        return None
    if last_time.tzinfo is None:
        last_time = last_time.replace(tzinfo=IST)

    diff_minutes = int((now - last_time).total_seconds() // 60)
    if diff_minutes < 60:
        return f"{diff_minutes} mins ago" if diff_minutes > 0 else "Just now"
    return last_time.strftime("%I:%M %p")


def format_clock_time(t: time) -> str:
    return datetime.combine(date.today(), t).strftime("%I:%M %p")


def build_streak(
    actuals_by_date: Dict[date, float],
    *,
    today: date,
    target_litres: float,
) -> List[DayStreak]:

    start = today - timedelta(days=STREAK_DAYS - 1)
    streak: List[DayStreak] = []
    for i in range(STREAK_DAYS):
        day = start + timedelta(days=i)
        actual = actuals_by_date.get(day, 0.0)
        if target_litres > 0 and actual > 0:
            pct = min(round((actual / target_litres) * 100, 1), 100.0)
        else:
            pct = 0.0
        label = "Today" if day == today else day.strftime("%a")
        streak.append(DayStreak(day=label, percentage=pct))
    return list(reversed(streak))


def seconds_until_midnight(now: datetime) -> int:
    midnight = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(int((midnight - now).total_seconds()), 1)


def vibration_pattern_for(reminder_type: str) -> Optional[List[int]]:
    return [0, 250, 250, 0] if reminder_type == "alarm" else None
