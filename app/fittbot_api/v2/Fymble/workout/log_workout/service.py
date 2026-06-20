"""Business logic for logging workouts.

Saves workout, calculates burnt calories, awards XP (5 per set, max 100/day),
saves workout_time, invalidates caches, and checks feedback status.
"""

import uuid
from datetime import date

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.cache_service import delete_keys_by_pattern

from .repository import LogWorkoutRepository
from .schemas import LogWorkoutRequest, LogWorkoutResponse, RemoveSetRequest, RemoveSetResponse

MAX_DAILY_XP = 100
XP_PER_SET = 5


class LogWorkoutService:
    """Log a workout and award XP."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis
        self.repo = LogWorkoutRepository(db)

    async def log_workout(self, client_id: int, data: LogWorkoutRequest) -> LogWorkoutResponse:
        today = date.today()

        # 1. Inject set_id into each set
        set_ids = self._inject_set_ids(data.workout_details)

        # 2. Calculate burnt calories
        total_burnt = self._calculate_burnt_calories(data.workout_details)

        # 3. Upsert actual_workout with workout_time
        record = await self.repo.get_workout_record(client_id, today)
        if record:
            await self.repo.append_workout(record, data.workout_details, data.workout_time)
        else:
            record = await self.repo.create_workout(
                client_id, today, data.workout_details, data.workout_time
            )

        # 4. Update client_actual burnt calories
        await self.repo.upsert_burnt_calories(client_id, today, total_burnt)

        # 5. Award XP (5 per set, capped at 100/day)
        xp_earned = await self._award_xp(client_id, data)

        # 6. Invalidate caches
        await delete_keys_by_pattern(self.redis, f"{client_id}:*:target_actual")
        await delete_keys_by_pattern(self.redis, f"{client_id}:*:chart")

        # 7. Commit
        await self.db.commit()

        # 8. Feedback check
        show_feedback = await self.repo.check_feedback_status(client_id)

        return LogWorkoutResponse(
            message="Workout data appended and updated",
            record_id=record.record_id,
            total_burnt_calories=total_burnt,
            xp_earned=xp_earned,
            feedback=show_feedback,
            set_ids=set_ids,
        )

    async def remove_set(self, client_id: int, data: RemoveSetRequest) -> RemoveSetResponse:
        today = date.today()

        # 1. Get today's workout record
        record = await self.repo.get_workout_record(client_id, today)
        if not record or not record.workout_details:
            raise ValueError("No workout found for today")

        # 2. Find and remove the set from the JSON
        workout_details = record.workout_details
        calories_removed = 0.0
        found = False

        for muscle_group in workout_details:
            for mg_name, exercises in list(muscle_group.items()):
                for exercise in exercises:
                    sets = exercise.get("sets", [])
                    for s in sets:
                        if s.get("set_id") == data.set_id:
                            try:
                                calories_removed = float(s.get("calories", 0) or 0)
                            except (ValueError, TypeError):
                                calories_removed = 0.0
                            sets.remove(s)
                            found = True
                            break
                    if found:
                        # Remove exercise if no sets left
                        if not sets:
                            exercises.remove(exercise)
                        break
                if found:
                    # Remove muscle group if no exercises left
                    if not exercises:
                        workout_details.remove(muscle_group)
                    break
            if found:
                break

        if not found:
            raise ValueError("Set not found")

        # 3. Update the workout record
        await self.repo.update_workout_details(record, workout_details)

        # 4. Subtract calories from client_actual
        if calories_removed > 0:
            await self.repo.subtract_burnt_calories(client_id, today, calories_removed)

        # 5. Subtract XP (5 per set removed)
        await self._subtract_xp(client_id, XP_PER_SET)

        # 6. Invalidate caches
        await delete_keys_by_pattern(self.redis, f"{client_id}:*:target_actual")
        await delete_keys_by_pattern(self.redis, f"{client_id}:*:chart")

        # 7. Commit
        await self.db.commit()

        return RemoveSetResponse(
            message="Set removed successfully",
            calories_removed=calories_removed,
        )

    async def _award_xp(self, client_id: int, data: LogWorkoutRequest) -> int:
        total_sets = self._count_sets(data.workout_details)
        calculated_xp = total_sets * XP_PER_SET

        today = date.today()
        event = await self.repo.get_or_create_calorie_event(client_id, today)

        already_earned = event.workout_added or 0
        if already_earned >= MAX_DAILY_XP:
            return 0

        xp = min(calculated_xp, MAX_DAILY_XP - already_earned)
        await self.repo.add_workout_xp(event, xp)
        return xp

    async def _subtract_xp(self, client_id: int, xp: int) -> None:
        today = date.today()
        event = await self.repo.get_or_create_calorie_event(client_id, today)
        current_xp = event.workout_added or 0
        new_xp = max(0, current_xp - xp)
        event.workout_added = new_xp
        await self.repo.db.flush()

    @staticmethod
    def _inject_set_ids(workout_details: list) -> list[str]:
        """Add a unique set_id to every set that doesn't already have one."""
        generated_ids = []
        for muscle_group in workout_details:
            for exercises in muscle_group.values():
                for exercise in exercises:
                    for set_detail in exercise.get("sets", []):
                        if not set_detail.get("set_id"):
                            sid = uuid.uuid4().hex[:8]
                            set_detail["set_id"] = sid
                            generated_ids.append(sid)
                        else:
                            generated_ids.append(set_detail["set_id"])
        return generated_ids

    @staticmethod
    def _calculate_burnt_calories(workout_details: list) -> float:
        total = 0.0
        for muscle_group in workout_details:
            for exercises in muscle_group.values():
                for exercise in exercises:
                    for set_detail in exercise.get("sets", []):
                        try:
                            total += float(set_detail.get("calories", 0) or 0)
                        except (ValueError, TypeError):
                            continue
        return total

    @staticmethod
    def _count_sets(workout_details: list) -> int:
        total = 0
        for muscle_group in workout_details:
            for exercises in muscle_group.values():
                for exercise in exercises:
                    total += len(exercise.get("sets", []))
        return total
