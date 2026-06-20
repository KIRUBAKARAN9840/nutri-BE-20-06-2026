"""Database queries for logging workouts.

Handles actual_workout upsert, client_actual burnt calories,
CalorieEvent XP tracking, and feedback status.
"""

from datetime import date
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models import (
    ActualWorkout,
    CalorieEvent,
    ClientActual,
    ClientFeedback,
)


class LogWorkoutRepository:
    """Workout logging data access."""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ── actual_workout ──

    async def get_workout_record(self, client_id: int, workout_date: date) -> Optional[ActualWorkout]:
        result = await self.db.execute(
            select(ActualWorkout).where(
                ActualWorkout.client_id == client_id,
                ActualWorkout.date == workout_date,
            )
        )
        return result.scalars().first()

    async def create_workout(
        self, client_id: int, workout_date: date, details: list, workout_time: Optional[float] = None
    ) -> ActualWorkout:
        record = ActualWorkout(
            client_id=client_id,
            date=workout_date,
            workout_details=details,
            workout_time=workout_time or 0,
        )
        self.db.add(record)
        await self.db.flush()
        return record

    async def append_workout(
        self, record: ActualWorkout, new_details: list, workout_time: Optional[float] = None
    ) -> None:
        if record.workout_details is None:
            record.workout_details = new_details
        elif isinstance(record.workout_details, list):
            record.workout_details = record.workout_details + new_details
        else:
            record.workout_details = [record.workout_details] + new_details

        current_time = float(record.workout_time) if record.workout_time else 0
        record.workout_time = current_time + (workout_time or 0)
        await self.db.flush()

    # ── fetch today's logged workouts for a muscle group ──

    async def get_todays_workout(self, client_id: int, today: date) -> Optional[ActualWorkout]:
        result = await self.db.execute(
            select(ActualWorkout).where(
                ActualWorkout.client_id == client_id,
                ActualWorkout.date == today,
            )
        )
        return result.scalars().first()

    async def get_previous_muscle_group_workout(
        self, client_id: int, muscle_group: str, before_date: date
    ) -> Optional[List[Dict[str, Any]]]:
        """Find the most recent workout before today that contains this muscle group."""
        result = await self.db.execute(
            select(ActualWorkout)
            .where(
                ActualWorkout.client_id == client_id,
                ActualWorkout.date < before_date,
            )
            .order_by(ActualWorkout.date.desc())
            .limit(10)
        )
        rows = result.scalars().all()

        for row in rows:
            if not row.workout_details:
                continue
            for entry in row.workout_details:
                if muscle_group in entry:
                    return entry[muscle_group]
        return None

    # ── client_actual burnt calories ──

    async def get_client_actual(self, client_id: int, workout_date: date) -> Optional[ClientActual]:
        result = await self.db.execute(
            select(ClientActual).where(
                ClientActual.client_id == client_id,
                ClientActual.date == workout_date,
            )
        )
        return result.scalars().first()

    async def update_workout_details(self, record: ActualWorkout, new_details: list) -> None:
        record.workout_details = new_details
        await self.db.flush()

    async def subtract_burnt_calories(self, client_id: int, workout_date: date, amount: float) -> None:
        client_actual = await self.get_client_actual(client_id, workout_date)
        if client_actual and client_actual.burnt_calories:
            current = float(client_actual.burnt_calories)
            client_actual.burnt_calories = max(0, current - amount)
            await self.db.flush()

    async def upsert_burnt_calories(self, client_id: int, workout_date: date, burnt: float) -> None:
        client_actual = await self.get_client_actual(client_id, workout_date)
        if client_actual:
            current = float(client_actual.burnt_calories) if client_actual.burnt_calories else 0
            client_actual.burnt_calories = current + burnt
        else:
            self.db.add(ClientActual(
                client_id=client_id,
                date=workout_date,
                burnt_calories=burnt,
            ))
        await self.db.flush()

    # ── calorie_event (XP cap tracking) ──

    async def get_or_create_calorie_event(self, client_id: int, event_date: date) -> CalorieEvent:
        result = await self.db.execute(
            select(CalorieEvent).where(
                CalorieEvent.client_id == client_id,
                CalorieEvent.event_date == event_date,
            )
        )
        event = result.scalars().first()
        if not event:
            event = CalorieEvent(
                client_id=client_id,
                event_date=event_date,
                workout_added=0,
            )
            self.db.add(event)
            await self.db.flush()
        return event

    async def add_workout_xp(self, event: CalorieEvent, xp: int) -> None:
        if not event.workout_added:
            event.workout_added = 0
        event.workout_added += xp
        await self.db.flush()

    # ── feedback status ──

    async def check_feedback_status(self, client_id: int) -> bool:
        result = await self.db.execute(
            select(ClientFeedback)
            .where(ClientFeedback.client_id == client_id)
            .order_by(ClientFeedback.updated_at.desc())
            .limit(1)
        )
        feedback = result.scalars().first()

        if not feedback:
            return True
        if feedback.status == "submitted":
            return False
        if feedback.status == "canceled":
            if feedback.next_feedback_date:
                return date.today() >= feedback.next_feedback_date
            return True
        if feedback.status == "pending":
            return True
        return False
