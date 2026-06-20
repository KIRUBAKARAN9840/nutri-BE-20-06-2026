"""Database & cache queries for Fittbot Workout.

Client gender cached in Redis. Muscle groups, exercise data,
and logged workout data fetched from DB.
"""

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Set

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models import ActualWorkout, Client, FittbotMuscleGroup, FittbotWorkout, HomeWorkout
from app.utils.logging_setup import jlog

CLIENT_GENDER_TTL = 86400  # 24 hours



class FittbotWorkoutRepository:
    """Fittbot workout data access."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis

    async def get_client_gender(self, client_id: int) -> Optional[str]:
        """Fetch gender from Redis cache, fallback to DB and cache it."""
        cached = await self._get_cached_gender(client_id)
        if cached:
            return cached

        result = await self.db.execute(
            select(Client.gender).where(Client.client_id == client_id)
        )
        gender = result.scalar_one_or_none()

        if gender:
            await self._cache_gender(client_id, gender)

        return gender

    async def get_muscle_groups_by_gender(self, gender: str) -> Sequence[FittbotMuscleGroup]:
        """Fetch all muscle group rows for the given gender."""
        result = await self.db.execute(
            select(FittbotMuscleGroup)
            .where(FittbotMuscleGroup.gender == gender)
            .order_by(FittbotMuscleGroup.id)
        )
        return result.scalars().all()

    async def get_exercise_data(self, category: str = "gym") -> Optional[Dict[str, Any]]:
        """Fetch exercise data JSON from fittbot_workout (gym) or home_workout (home)."""
        if category == "home":
            result = await self.db.execute(
                select(HomeWorkout.home_workout).limit(1)
            )
        else:
            result = await self.db.execute(
                select(FittbotWorkout.exercise_data).limit(1)
            )
        return result.scalar_one_or_none()

    # ── Logged workout queries ──

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
        """Find the most recent workout before today containing this muscle group."""
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

    async def get_workout_by_date(self, client_id: int, workout_date: date) -> Optional[ActualWorkout]:
        """Fetch workout for a specific date."""
        result = await self.db.execute(
            select(ActualWorkout).where(
                ActualWorkout.client_id == client_id,
                ActualWorkout.date == workout_date,
            )
        )
        return result.scalars().first()

    async def get_workouts_date_range(
        self, client_id: int, start_date: date, end_date: date
    ) -> List[ActualWorkout]:
        """Fetch all workouts between two dates."""
        result = await self.db.execute(
            select(ActualWorkout)
            .where(
                ActualWorkout.client_id == client_id,
                ActualWorkout.date >= start_date,
                ActualWorkout.date <= end_date,
            )
            .order_by(ActualWorkout.date.desc())
        )
        return result.scalars().all()

    # ── Streak & history ──

    async def get_last_7_days_dates(self, client_id: int, today: date) -> Set[date]:
        """Return set of dates in last 7 days that have a workout log."""
        week_ago = today - timedelta(days=6)
        result = await self.db.execute(
            select(ActualWorkout.date).where(
                ActualWorkout.client_id == client_id,
                ActualWorkout.date >= week_ago,
                ActualWorkout.date <= today,
            )
        )
        return {row[0] for row in result.all()}

    async def get_recent_workouts(self, client_id: int, today: date, days: int = 21) -> List[ActualWorkout]:
        """Fetch workouts from last N days for split detection."""
        start = today - timedelta(days=days)
        result = await self.db.execute(
            select(ActualWorkout)
            .where(
                ActualWorkout.client_id == client_id,
                ActualWorkout.date >= start,
                ActualWorkout.date <= today,
            )
            .order_by(ActualWorkout.date.desc())
        )
        return result.scalars().all()

    # ── Redis helpers ──

    async def _get_cached_gender(self, client_id: int) -> Optional[str]:
        try:
            data = await self.redis.get(f"{client_id}:gender")
            if data:
                return data.decode() if isinstance(data, bytes) else data
        except RedisError as e:
            jlog("warning", {
                "type": "cache_read_failure",
                "error_code": "FITTBOT_WORKOUT_GENDER_CACHE_READ",
                "detail": str(e),
                "client_id": client_id,
            })
        return None

    async def _cache_gender(self, client_id: int, gender: str) -> None:
        try:
            key = f"{client_id}:gender"
            await self.redis.set(key, gender)
            await self.redis.expire(key, CLIENT_GENDER_TTL)
        except RedisError as e:
            jlog("warning", {
                "type": "cache_write_failure",
                "error_code": "FITTBOT_WORKOUT_GENDER_CACHE_WRITE",
                "detail": str(e),
                "client_id": client_id,
            })
