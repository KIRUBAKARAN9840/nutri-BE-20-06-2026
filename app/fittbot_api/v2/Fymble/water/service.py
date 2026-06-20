"""Business logic for Water Tracker.

Handles get, add, and set-target for daily water intake.
"""

from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.logging_setup import jlog
from .repository import WaterRepository
from app.utils.logging_utils import FittbotHTTPException
from .schemas import (
    AddWaterResponse,
    SetWaterTargetRequest,
    SetWaterTargetResponse,
    SetWaterReminderRequest,
    SetWaterReminderResponse,
    DeleteWaterReminderResponse,
    GetWaterResponse,
    WaterData,
    WaterIntakeData,
    WaterReminderData,
    DayStreak,
)

import json

GLASS_ML = 250
WATER_CACHE_KEY = "{client_id}:{date}:water_tracker"
WATER_REMINDER_CACHE_KEY = "{client_id}:{date}:water_reminder"

_IST = ZoneInfo("Asia/Kolkata")


class WaterService:

    def __init__(self, db: AsyncSession, redis: Redis):
        self.repo = WaterRepository(db)
        self.redis = redis

    # ── GET water ─────────────────────────────────────────────────

    async def get_water(self, client_id: int) -> GetWaterResponse:
        now = datetime.now(_IST)
        today = now.date()
        today_str = today.isoformat()
        cache_key = WATER_CACHE_KEY.format(client_id=client_id, date=today_str)
        reminder_key = WATER_REMINDER_CACHE_KEY.format(client_id=client_id, date=today_str)

        # Fetch reminder (cached separately)
        reminder_data = await self._get_reminder_data(client_id, reminder_key)

        # Try cache first (has target, actual, streak — NOT last_drink_time)
        cached = await self._get_cache(cache_key)
        if cached:
            # last_drink_time is always fresh from DB
            actual_row = await self.repo.get_actual_today(client_id, today)
            last_drink = self._format_last_drink_time(
                actual_row.last_water_time if actual_row else None, now,
            )
            return GetWaterResponse(
                data=WaterData(
                    water_intake=WaterIntakeData(**cached["water_intake"]),
                    last_drink_time=last_drink,
                    streak=[DayStreak(**d) for d in cached["streak"]],
                    reminder=reminder_data,
                )
            )

        # Cache miss — build from DB
        target_row = await self.repo.get_target(client_id)
        actual_row = await self.repo.get_actual_today(client_id, today)

        target_val = target_row.water_intake if target_row and target_row.water_intake else 0
        actual_val = actual_row.water_intake if actual_row and actual_row.water_intake else 0
        last_drink = self._format_last_drink_time(actual_row.last_water_time if actual_row else None, now)
        streak = await self._build_streak(client_id, today, target_val)

        # Cache everything except last_drink_time
        await self._set_cache(cache_key, {
            "water_intake": {"target": target_val, "actual": actual_val},
            "streak": [s.model_dump() for s in streak],
        })

        return GetWaterResponse(
            data=WaterData(
                water_intake=WaterIntakeData(target=target_val, actual=actual_val),
                last_drink_time=last_drink,
                streak=streak,
                reminder=reminder_data,
            )
        )

    # ── ADD water ─────────────────────────────────────────────────

    async def add_water(self, client_id: int) -> AddWaterResponse:
        now = datetime.now(_IST)
        today = now.date()

        actual_row = await self.repo.get_actual_today(client_id, today)
        current = actual_row.water_intake if actual_row and actual_row.water_intake else 0
        new_total = current + (GLASS_ML / 1000)  # 250ml → 0.25 litres

        await self.repo.upsert_actual_water(client_id, today, new_total, now)

        xp_earned = await self._award_xp(client_id, today, new_total)

        await self._invalidate_caches(client_id)
        return AddWaterResponse(xp_earned=xp_earned)

    # ── Water XP ──────────────────────────────────────────────────

    async def _award_xp(self, client_id: int, today, actual_water: float) -> int:
        """Award XP based on actual/target water ratio * 50, capped at 50/day."""
        from app.models.fittbot_models import CalorieEvent, ClientTarget

        MAX_DAILY_WATER_XP = 50

        # Get water target
        result = await self.repo.db.execute(
            select(ClientTarget).where(ClientTarget.client_id == client_id)
        )
        client_target = result.scalars().first()
        target_water = client_target.water_intake if client_target and client_target.water_intake else 0

        if target_water <= 0:
            return 0

        ratio = min(actual_water / target_water, 1.0)
        calculated_xp = int(round(ratio * MAX_DAILY_WATER_XP))

        # Get or create calorie event
        result = await self.repo.db.execute(
            select(CalorieEvent).where(
                CalorieEvent.client_id == client_id,
                CalorieEvent.event_date == today,
            )
        )
        calorie_event = result.scalars().first()

        if not calorie_event:
            calorie_event = CalorieEvent(client_id=client_id, event_date=today, water_added=0)
            self.repo.db.add(calorie_event)
            await self.repo.db.flush()

        added = calorie_event.water_added or 0
        if added >= MAX_DAILY_WATER_XP:
            return 0

        # Award the difference (so XP always reflects the current ratio, not stacking)
        points_to_award = min(calculated_xp - added, MAX_DAILY_WATER_XP - added)
        if points_to_award <= 0:
            return 0

        calorie_event.water_added = added + points_to_award

        await self.repo.db.commit()
        return points_to_award

    # ── SET target ────────────────────────────────────────────────

    async def set_target(self, client_id: int, req: SetWaterTargetRequest) -> SetWaterTargetResponse:
        await self.repo.upsert_target_water(client_id, req.target_water)
        await self._invalidate_caches(client_id)
        return SetWaterTargetResponse()

    # ── SET water reminder ─────────────────────────────────────────

    async def set_reminder(self, client_id: int, req: SetWaterReminderRequest) -> SetWaterReminderResponse:
        now = datetime.now(_IST)
        current_t = now.replace(tzinfo=None).time()  # naive for comparison with request times

        if req.intimation_start_time >= req.intimation_end_time:
            raise FittbotHTTPException(
                status_code=400,
                detail="Start time must be before end time",
                error_code="WATER_REMINDER_INVALID_WINDOW",
            )


        # Compute first reminder time
        if req.is_recurring and not (req.intimation_start_time <= current_t <= req.intimation_end_time):
            # Outside window for recurring → schedule at start of window
            first_reminder_dt = datetime.combine(now.date(), req.intimation_start_time)
            reminder_sent = True  # don't fire until window opens
        else:
            first_reminder_dt = self._compute_next_water_time(
                req.intimation_start_time, req.water_timing, req.intimation_end_time, now,
            )
            reminder_sent = False

        vibration_pattern = [0, 250, 250, 0] if req.reminder_type == "alarm" else None

        # Remove existing water reminders, then create new one
        await self.repo.delete_water_reminders(client_id)
        reminder = await self.repo.create_water_reminder(
            client_id=client_id,
            reminder_time=first_reminder_dt.time(),
            reminder_type=req.reminder_type,
            is_recurring=req.is_recurring,
            water_timing=req.water_timing,
            intimation_start_time=req.intimation_start_time,
            intimation_end_time=req.intimation_end_time,
            reminder_sent=reminder_sent,
            details="Drink 250ml of water",
            title="Water Reminder",
            vibration_pattern=vibration_pattern,
        )

        await self._invalidate_caches(client_id)

        return SetWaterReminderResponse(
            reminder_id=reminder.reminder_id,
            scheduled_reminder_time=first_reminder_dt.strftime("%I:%M %p"),
        )

    # ── DELETE water reminder ────────────────────────────────────

    async def delete_reminder(self, client_id: int) -> DeleteWaterReminderResponse:
        await self.repo.delete_water_reminders(client_id)
        await self._invalidate_caches(client_id)
        return DeleteWaterReminderResponse()

    # ── Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _compute_next_water_time(
        start_time, water_timing: float, end_time, now: datetime,
    ) -> datetime:
        """Snap to next 30-min boundary, respecting the intimation window."""
        now = now.replace(tzinfo=None)  # work naive to match request times
        if now.minute < 30:
            boundary = now.replace(minute=30, second=0, microsecond=0)
        else:
            boundary = (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))

        end_dt = datetime.combine(now.date(), end_time)
        if boundary > end_dt:
            boundary = end_dt

        if water_timing < 1:
            return boundary

        next_dt = boundary if boundary.minute == 0 else boundary + timedelta(minutes=30)
        if next_dt > end_dt:
            next_dt = end_dt
        return next_dt

    @staticmethod
    def _format_last_drink_time(last_time: Optional[datetime], now: datetime) -> Optional[str]:
        if not last_time:
            return None

        # DB stores naive datetime — treat it as IST to match `now`
        if last_time.tzinfo is None:
            last_time = last_time.replace(tzinfo=_IST)

        diff = now - last_time
        total_minutes = int(diff.total_seconds() // 60)

        if total_minutes < 60:
            return f"{total_minutes} mins ago" if total_minutes > 0 else "Just now"

        return last_time.strftime("%I:%M %p")

    async def _build_streak(self, client_id: int, today, target: float) -> list:
        start = today - timedelta(days=6)
        rows = await self.repo.get_last_7_days(client_id, start, today)

        actual_by_date = {}
        for row in rows:
            if row.water_intake and row.water_intake > 0:
                actual_by_date[row.date] = row.water_intake

        streak = []
        for i in range(7):
            day = start + timedelta(days=i)
            actual = actual_by_date.get(day, 0)
            pct = min(round((actual / target) * 100, 1), 100.0) if target > 0 and actual > 0 else 0
            label = "Today" if day == today else day.strftime("%a")  # Mon, Tue, Wed...
            streak.append(DayStreak(day=label, percentage=pct))

        return streak[::-1]

    # ── Reminder helper ────────────────────────────────────────────

    async def _get_reminder_data(self, client_id: int, reminder_key: str) -> WaterReminderData:
        cached = await self._get_cache(reminder_key)
        if cached:
            return WaterReminderData(**cached)

        row = await self.repo.get_water_reminder(client_id)
        if not row:
            data = WaterReminderData()
        else:
            data = WaterReminderData(
                is_enabled=True,
                water_timing=row.water_timing,
                intimation_start_time=row.intimation_start_time.strftime("%I:%M %p") if row.intimation_start_time else None,
                intimation_end_time=row.intimation_end_time.strftime("%I:%M %p") if row.intimation_end_time else None,
                is_recurring=row.is_recurring if row.is_recurring else False,
            )

        await self._set_cache(reminder_key, data.model_dump())
        return data

    # ── Redis cache helpers ──────────────────────────────────────

    async def _get_cache(self, key: str) -> Optional[dict]:
        try:
            raw = await self.redis.get(key)
            if raw:
                return json.loads(raw)
        except RedisError:
            pass
        return None

    def _seconds_until_midnight(self) -> int:
        now = datetime.now(_IST)
        midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return max(int((midnight - now).total_seconds()), 1)

    async def _set_cache(self, key: str, data: dict) -> None:
        try:
            ttl = self._seconds_until_midnight()
            await self.redis.setex(key, ttl, json.dumps(data))
        except RedisError:
            pass

    # ── Cache invalidation ────────────────────────────────────────

    async def _invalidate_caches(self, client_id: int) -> None:
        today_str = datetime.now(_IST).date().isoformat()
        keys_to_delete = [
            f"client{client_id}:initial_target_actual",
            f"client{client_id}:initialstatus",
            f"{client_id}:target_actual:{today_str}",
            WATER_CACHE_KEY.format(client_id=client_id, date=today_str),
            WATER_REMINDER_CACHE_KEY.format(client_id=client_id, date=today_str),
        ]
        try:
            await self.redis.delete(*keys_to_delete)
        except RedisError as e:
            jlog("warning", {
                "type": "cache_invalidate_failure",
                "error_code": "WATER_CACHE_INVALIDATE",
                "detail": str(e),
                "client_id": client_id,
            })
