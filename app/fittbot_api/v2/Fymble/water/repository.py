"""Database queries for Water Tracker.

Only data access lives here — no business logic.
"""

from datetime import date as date_type, datetime, time
from typing import Optional, List

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models import ClientTarget, ClientActual, Reminder


class WaterRepository:

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── Reads ─────────────────────────────────────────────────────

    async def get_target(self, client_id: int) -> Optional[ClientTarget]:
        result = await self.db.execute(
            select(ClientTarget).where(ClientTarget.client_id == client_id)
        )
        return result.scalars().first()

    async def get_actual_today(self, client_id: int, today: date_type) -> Optional[ClientActual]:
        result = await self.db.execute(
            select(ClientActual).where(
                ClientActual.client_id == client_id,
                ClientActual.date == today,
            )
        )
        return result.scalars().first()

    async def get_last_7_days(self, client_id: int, start: date_type, end: date_type) -> List[ClientActual]:
        result = await self.db.execute(
            select(ClientActual).where(
                ClientActual.client_id == client_id,
                ClientActual.date >= start,
                ClientActual.date <= end,
            )
        )
        return list(result.scalars().all())

    # ── Writes: water intake ──────────────────────────────────────

    async def upsert_actual_water(self, client_id: int, today: date_type, water: float, now: datetime) -> None:
        existing = await self.get_actual_today(client_id, today)
        if existing:
            existing.water_intake = water
            existing.last_water_time = now
        else:
            self.db.add(ClientActual(
                client_id=client_id,
                date=today,
                water_intake=water,
                last_water_time=now,
            ))
        await self.db.commit()

    # ── Writes: water target ──────────────────────────────────────

    async def upsert_target_water(self, client_id: int, target_water: float) -> None:
        existing = await self.get_target(client_id)
        if existing:
            existing.water_intake = target_water
        else:
            self.db.add(ClientTarget(
                client_id=client_id,
                water_intake=target_water,
            ))
        await self.db.commit()

    # ── Reads: water reminder ───────────────────────────────────

    async def get_water_reminder(self, client_id: int) -> Optional[Reminder]:
        result = await self.db.execute(
            select(Reminder).where(
                Reminder.client_id == client_id,
                Reminder.reminder_mode == "water",
            )
        )
        return result.scalars().first()

    # ── Writes: water reminder ────────────────────────────────────

    async def delete_water_reminders(self, client_id: int) -> None:
        await self.db.execute(
            delete(Reminder).where(
                Reminder.client_id == client_id,
                Reminder.reminder_mode == "water",
            )
        )
        await self.db.commit()

    async def create_water_reminder(
        self,
        client_id: int,
        reminder_time: time,
        reminder_type: str,
        is_recurring: bool,
        water_timing: float,
        intimation_start_time: time,
        intimation_end_time: time,
        reminder_sent: bool,
        details: str,
        title: str,
        vibration_pattern: Optional[list],
    ) -> Reminder:
        reminder = Reminder(
            client_id=client_id,
            reminder_time=reminder_time,
            reminder_type=reminder_type,
            is_recurring=is_recurring,
            reminder_mode="water",
            water_timing=water_timing,
            water_amount=250,
            intimation_start_time=intimation_start_time,
            intimation_end_time=intimation_end_time,
            reminder_Sent=reminder_sent,
            details=details,
            title=title,
            vibration_pattern=vibration_pattern,
        )
        self.db.add(reminder)
        await self.db.commit()
        await self.db.refresh(reminder)
        return reminder
    



