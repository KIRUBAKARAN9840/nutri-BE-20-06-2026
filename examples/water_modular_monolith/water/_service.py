"""Application service — the orchestrator.

This is the only file that knows the full sequence of "fetch → compute
→ persist → invalidate → publish event." Each step delegates to a port:

    repository  →  the DB
    cache       →  Redis
    event_bus   →  cross-module side effects

Notice what this file does NOT do:
  - import SQLAlchemy
  - import redis
  - know about other modules' cache keys
  - touch ClientTarget/CalorieEvent for XP (that moved to a subscriber
    on `WaterIntakeAdded` — see `_events.py`)
"""

from datetime import date as date_type, datetime, time, timedelta
from typing import Optional

from app.utils.logging_utils import FittbotHTTPException

from .schemas import (
    DayStreak,
    WaterIntakeData,
    WaterReminderCreated,
    WaterReminderData,
    WaterStatus,
)
from . import _domain
from ._cache import WaterCache
from ._events import EventBus, WaterIntakeAdded, WaterTargetSet
from ._repository import (
    ActualWaterRow,
    ReminderRow,
    WaterRepository,
)


class WaterService:
    """Implements the WaterAPI Protocol. Constructed via build_water_api."""

    def __init__(
        self,
        *,
        repository: WaterRepository,
        cache: WaterCache,
        event_bus: EventBus,
    ):
        self._repo = repository
        self._cache = cache
        self._bus = event_bus

    # ─── get ──────────────────────────────────────────────────────────

    async def get_status(self, client_id: int) -> WaterStatus:
        now = _domain.now_ist()
        today = now.date()

        reminder = await self._resolve_reminder(client_id, today)
        cached = await self._cache.get_status(client_id, today)
        if cached is not None:
            actual = await self._repo.get_actual_today(client_id, today)
            last_drink = _domain.format_last_drink_time(
                actual.last_water_time if actual else None, now,
            )
            return WaterStatus(
                intake=WaterIntakeData(**cached["intake"]),
                last_drink_time=last_drink,
                streak=[DayStreak(**d) for d in cached["streak"]],
                reminder=reminder,
            )

        target = await self._repo.get_target_litres(client_id) or 0.0
        actual = await self._repo.get_actual_today(client_id, today)
        actual_litres = actual.water_intake if actual else 0.0
        last_drink = _domain.format_last_drink_time(
            actual.last_water_time if actual else None, now,
        )
        last_7 = await self._repo.get_actuals_in_range(
            client_id, today - timedelta(days=_domain.STREAK_DAYS - 1), today,
        )
        streak = _domain.build_streak(last_7, today=today, target_litres=target)

        await self._cache.set_status(
            client_id, today,
            {
                "intake": {"target_litres": target, "actual_litres": actual_litres},
                "streak": [s.model_dump() for s in streak],
            },
            ttl=_domain.seconds_until_midnight(now),
        )
        return WaterStatus(
            intake=WaterIntakeData(target_litres=target, actual_litres=actual_litres),
            last_drink_time=last_drink,
            streak=streak,
            reminder=reminder,
        )

    # ─── log glass ────────────────────────────────────────────────────

    async def log_glass(self, client_id: int) -> float:
        now = _domain.now_ist()
        today = now.date()

        actual = await self._repo.get_actual_today(client_id, today)
        current = actual.water_intake if actual else 0.0
        new_total = _domain.add_one_glass(current)

        await self._repo.upsert_actual(client_id, today, new_total, now)
        target = await self._repo.get_target_litres(client_id) or 0.0
        await self._cache.invalidate(client_id, today)

        await self._bus.publish(WaterIntakeAdded(
            client_id=client_id,
            total_litres=new_total,
            target_litres=target,
            occurred_at=now,
        ))
        return new_total

    # ─── set target ───────────────────────────────────────────────────

    async def set_target_litres(self, client_id: int, litres: float) -> None:
        await self._repo.upsert_target(client_id, litres)
        today = _domain.now_ist().date()
        await self._cache.invalidate(client_id, today)
        await self._bus.publish(WaterTargetSet(
            client_id=client_id,
            target_litres=litres,
            occurred_at=_domain.now_ist(),
        ))

    # ─── reminder ─────────────────────────────────────────────────────

    async def set_reminder(
        self,
        client_id: int,
        *,
        reminder_type: str,
        is_recurring: bool,
        water_timing: float,
        intimation_start_time: time,
        intimation_end_time: time,
    ) -> WaterReminderCreated:
        if intimation_start_time >= intimation_end_time:
            raise FittbotHTTPException(
                status_code=400,
                detail="Start time must be before end time",
                error_code="WATER_REMINDER_INVALID_WINDOW",
            )

        now = _domain.now_ist()
        current_t = now.replace(tzinfo=None).time()

        if is_recurring and _domain.is_outside_window(
            intimation_start_time, intimation_end_time, current_t,
        ):
            first_dt = datetime.combine(now.date(), intimation_start_time)
            already_sent = True
        else:
            first_dt = _domain.compute_next_reminder_time(
                start_time=intimation_start_time,
                end_time=intimation_end_time,
                water_timing=water_timing,
                now=now,
            )
            already_sent = False

        await self._repo.delete_water_reminders(client_id)
        reminder_id = await self._repo.create_water_reminder(
            client_id=client_id,
            reminder_time=first_dt.time(),
            reminder_type=reminder_type,
            is_recurring=is_recurring,
            water_timing=water_timing,
            intimation_start_time=intimation_start_time,
            intimation_end_time=intimation_end_time,
            reminder_sent=already_sent,
            vibration_pattern=_domain.vibration_pattern_for(reminder_type),
        )

        await self._cache.invalidate(client_id, now.date())
        return WaterReminderCreated(
            reminder_id=reminder_id,
            scheduled_reminder_time=first_dt.strftime("%I:%M %p"),
        )

    async def delete_reminder(self, client_id: int) -> None:
        await self._repo.delete_water_reminders(client_id)
        await self._cache.invalidate(client_id, _domain.now_ist().date())

    # ─── helpers ──────────────────────────────────────────────────────

    async def _resolve_reminder(
        self, client_id: int, today: date_type,
    ) -> WaterReminderData:
        cached = await self._cache.get_reminder(client_id, today)
        if cached is not None:
            return WaterReminderData(**cached)

        row: Optional[ReminderRow] = await self._repo.get_water_reminder(client_id)
        if row is None:
            data = WaterReminderData()
        else:
            data = WaterReminderData(
                is_enabled=True,
                water_timing=row.water_timing,
                intimation_start_time=_domain.format_clock_time(row.intimation_start_time)
                    if row.intimation_start_time else None,
                intimation_end_time=_domain.format_clock_time(row.intimation_end_time)
                    if row.intimation_end_time else None,
                is_recurring=row.is_recurring,
            )

        await self._cache.set_reminder(
            client_id, today, data.model_dump(),
            ttl=_domain.seconds_until_midnight(_domain.now_ist()),
        )
        return data
