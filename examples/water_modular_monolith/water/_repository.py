

from dataclasses import dataclass
from datetime import date as date_type, datetime, time
from typing import Dict, Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models import ClientActual, ClientTarget, Reminder


@dataclass(frozen=True)
class ActualWaterRow:
    water_intake: float
    last_water_time: Optional[datetime]


@dataclass(frozen=True)
class ReminderRow:
    reminder_id: int
    water_timing: Optional[float]
    intimation_start_time: Optional[time]
    intimation_end_time: Optional[time]
    is_recurring: bool


class WaterRepository:
    """Plain CRUD over water-owned columns. No business logic."""

    def __init__(self, db: AsyncSession):
        self._db = db

    # ─── reads ────────────────────────────────────────────────────────

    async def get_target_litres(self, client_id: int) -> Optional[float]:
        result = await self._db.execute(
            select(ClientTarget.water_intake).where(ClientTarget.client_id == client_id)
        )
        return result.scalar()

    async def get_actual_today(
        self, client_id: int, today: date_type,
    ) -> Optional[ActualWaterRow]:
        result = await self._db.execute(
            select(ClientActual.water_intake, ClientActual.last_water_time).where(
                ClientActual.client_id == client_id,
                ClientActual.date == today,
            )
        )
        row = result.first()
        if row is None:
            return None
        return ActualWaterRow(
            water_intake=row.water_intake or 0.0,
            last_water_time=row.last_water_time,
        )

    async def get_actuals_in_range(
        self, client_id: int, start: date_type, end: date_type,
    ) -> Dict[date_type, float]:
        result = await self._db.execute(
            select(ClientActual.date, ClientActual.water_intake).where(
                ClientActual.client_id == client_id,
                ClientActual.date >= start,
                ClientActual.date <= end,
            )
        )
        return {
            row.date: row.water_intake
            for row in result.all()
            if row.water_intake and row.water_intake > 0
        }

    async def get_water_reminder(self, client_id: int) -> Optional[ReminderRow]:
        result = await self._db.execute(
            select(Reminder).where(
                Reminder.client_id == client_id,
                Reminder.reminder_mode == "water",
            )
        )
        row = result.scalars().first()
        if row is None:
            return None
        return ReminderRow(
            reminder_id=row.reminder_id,
            water_timing=row.water_timing,
            intimation_start_time=row.intimation_start_time,
            intimation_end_time=row.intimation_end_time,
            is_recurring=bool(row.is_recurring),
        )

    # ─── writes ───────────────────────────────────────────────────────

    async def upsert_target(self, client_id: int, target_litres: float) -> None:
        existing = await self._db.execute(
            select(ClientTarget).where(ClientTarget.client_id == client_id)
        )
        row = existing.scalars().first()
        if row:
            row.water_intake = target_litres
        else:
            self._db.add(ClientTarget(
                client_id=client_id, water_intake=target_litres,
            ))
        await self._db.commit()

    async def upsert_actual(
        self, client_id: int, today: date_type, litres: float, now: datetime,
    ) -> None:
        existing = await self._db.execute(
            select(ClientActual).where(
                ClientActual.client_id == client_id,
                ClientActual.date == today,
            )
        )
        row = existing.scalars().first()
        if row:
            row.water_intake = litres
            row.last_water_time = now
        else:
            self._db.add(ClientActual(
                client_id=client_id,
                date=today,
                water_intake=litres,
                last_water_time=now,
            ))
        await self._db.commit()

    async def delete_water_reminders(self, client_id: int) -> None:
        await self._db.execute(
            delete(Reminder).where(
                Reminder.client_id == client_id,
                Reminder.reminder_mode == "water",
            )
        )
        await self._db.commit()

    async def create_water_reminder(
        self,
        *,
        client_id: int,
        reminder_time: time,
        reminder_type: str,
        is_recurring: bool,
        water_timing: float,
        intimation_start_time: time,
        intimation_end_time: time,
        reminder_sent: bool,
        vibration_pattern: Optional[list],
    ) -> int:
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
            details="Drink 250ml of water",
            title="Water Reminder",
            vibration_pattern=vibration_pattern,
        )
        self._db.add(reminder)
        await self._db.commit()
        await self._db.refresh(reminder)
        return reminder.reminder_id
