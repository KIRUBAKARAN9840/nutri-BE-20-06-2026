"""Business logic for Fittbot Workout.

Resolves client gender and returns muscle groups / exercises
with the correct gender-specific assets + today's logged data, report,
streak, and suggested muscle groups.
"""

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.utils.logging_utils import FittbotHTTPException

from .repository import FittbotWorkoutRepository
from .schemas import (
    DayReport,
    ExerciseDetailData,
    ExerciseDetailResponse,
    ExerciseItem,
    ExerciseListResponse,
    FittbotWorkoutResponse,
    HistoryMuscleGroup,
    LoggedExerciseItem,
    LoggedSetItem,
    MuscleGroupItem,
    MuscleGroupReport,
    TodayReport,
    TodayReportResponse,
    StreakDay,
    SuggestedMuscleGroup,
    WorkoutHistoryResponse,
)


# Canonical muscle group order for the strength groups (Push/Pull/Legs style)
STRENGTH_ROTATION = ["Chest", "Back", "Leg", "Shoulder", "Biceps", "Triceps", "Forearms"]
DEFAULT_SUGGESTIONS = [
    ("Chest", "Start with a push day"),
    ("Back", "Follow up with a pull day"),
]


class FittbotWorkoutService:
    """Fetch muscle groups and exercises with gender-specific assets."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.repo = FittbotWorkoutRepository(db, redis)

    # ── helpers ──

    async def _resolve_gender(self, client_id: int) -> str:
        gender = await self.repo.get_client_gender(client_id)
        if not gender or gender.lower() not in ("male", "female"):
            return "male"
        return gender.lower()

    def _get_group_or_raise(
        self, exercise_data: Dict[str, Any], muscle_group: str, client_id: int
    ) -> Dict[str, Any]:
        if muscle_group not in exercise_data:
            raise FittbotHTTPException(
                status_code=404,
                detail=f"Muscle group '{muscle_group}' not found.",
                error_code="FITTBOT_MUSCLE_GROUP_INVALID",
                log_data={"client_id": client_id, "muscle_group": muscle_group},
            )
        return exercise_data[muscle_group]

    async def _get_exercise_data_or_raise(self, client_id: int, category: str = "gym") -> Dict[str, Any]:
        exercise_data = await self.repo.get_exercise_data(category)
        if not exercise_data:
            raise FittbotHTTPException(
                status_code=404,
                detail="No workout data found.",
                error_code="FITTBOT_EXERCISE_DATA_NOT_FOUND",
                log_data={"client_id": client_id},
            )
        return exercise_data

    CARDIO_GROUPS = {"Cardio", "Cycling", "Treadmill"}
    BODY_WEIGHT_GROUPS = {"ABS", "Core"}

    @staticmethod
    def _resolve_exercise_type(muscle_group: str, is_cardio: bool) -> str:
        if is_cardio or muscle_group in FittbotWorkoutService.CARDIO_GROUPS:
            return "cardio"
        if muscle_group in FittbotWorkoutService.BODY_WEIGHT_GROUPS:
            return "body_weight"
        return "strength"

    # ── 1. Muscle groups list ──

    async def get_muscle_groups(self, client_id: int) -> FittbotWorkoutResponse:
        resolved_gender = await self._resolve_gender(client_id)

        muscle_rows = await self.repo.get_muscle_groups_by_gender(resolved_gender)

        if not muscle_rows:
            raise FittbotHTTPException(
                status_code=404,
                detail="No muscle group data found.",
                error_code="FITTBOT_MUSCLE_GROUPS_NOT_FOUND",
                log_data={"client_id": client_id, "gender": resolved_gender},
            )

        data = [
            MuscleGroupItem(
                id=idx,
                muscle_group=row.muscle_group,
                image=row.url,
            )
            for idx, row in enumerate(muscle_rows, start=1)
        ]

        return FittbotWorkoutResponse(data=data)

    # ── 2. Exercises list for a muscle group ──

    async def get_exercises(self, client_id: int, muscle_group: str, category: str = "gym") -> ExerciseListResponse:
        resolved_gender = await self._resolve_gender(client_id)
        exercise_data = await self._get_exercise_data_or_raise(client_id, category)
        group_data = self._get_group_or_raise(exercise_data, muscle_group, client_id)

        is_cardio = group_data.get("isCardio", False)
        exercises = group_data.get("exercises", [])
        image_key = "imgPath" if resolved_gender == "male" else "imgPathFemale"
        exercise_type = self._resolve_exercise_type(muscle_group, is_cardio)

        today = date.today()
        todays_record = await self.repo.get_todays_workout(client_id, today)
        logged_map = self._extract_logged_exercises(todays_record, muscle_group)

        # Build exercise list
        data = []
        for ex in exercises:
            logged_sets = logged_map.get(ex["name"])
            data.append(
                ExerciseItem(
                    id=ex["id"],
                    name=ex["name"],
                    image=ex.get(image_key, ""),
                    type=exercise_type,
                    logged=logged_sets,
                )
            )

        # Report
        report = None
        if logged_map:
            report = await self._build_report(
                client_id, muscle_group, logged_map, todays_record, len(exercises), today
            )

        # Streak
        streak = await self._build_streak(client_id, today)

        # Suggested muscle groups
        suggested_tmw, suggested_dat = await self._build_suggestions(
            client_id, muscle_group, today, resolved_gender
        )

        return ExerciseListResponse(
            muscle_group=muscle_group,
            is_cardio=is_cardio,
            report=report,
            streak=streak,
            suggested_tomorrow=suggested_tmw,
            suggested_day_after=suggested_dat,
            data=data,
        )

    # ── 3. Single exercise detail by id ──

    async def get_exercise_detail(
        self, client_id: int, muscle_group: str, exercise_id: int, category: str = "gym"
    ) -> ExerciseDetailResponse:
        resolved_gender = await self._resolve_gender(client_id)
        exercise_data = await self._get_exercise_data_or_raise(client_id, category)
        group_data = self._get_group_or_raise(exercise_data, muscle_group, client_id)

        is_cardio = group_data.get("isCardio", False)
        exercises = group_data.get("exercises", [])
        image_key = "imgPath" if resolved_gender == "male" else "imgPathFemale"
        gif_key = "gifPath" if resolved_gender == "male" else "gifPathFemale"
        exercise_type = self._resolve_exercise_type(muscle_group, is_cardio)

        matched = None
        for ex in exercises:
            if ex["id"] == exercise_id:
                matched = ex
                break

        if not matched:
            raise FittbotHTTPException(
                status_code=404,
                detail=f"Exercise id {exercise_id} not found in '{muscle_group}'.",
                error_code="FITTBOT_EXERCISE_NOT_FOUND",
                log_data={
                    "client_id": client_id,
                    "muscle_group": muscle_group,
                    "exercise_id": exercise_id,
                },
            )

        max_id = max(ex["id"] for ex in exercises)
        has_next = exercise_id < max_id

        # Find next exercise name
        next_exercise_name = None
        if has_next:
            for ex in exercises:
                if ex["id"] == exercise_id + 1:
                    next_exercise_name = ex["name"]
                    break

        today = date.today()
        todays_record = await self.repo.get_todays_workout(client_id, today)
        logged_map = self._extract_logged_exercises(todays_record, muscle_group)
        logged_sets = logged_map.get(matched["name"])

        return ExerciseDetailResponse(
            muscle_group=muscle_group,
            data=ExerciseDetailData(
                id=matched["id"],
                name=matched["name"],
                gif=matched.get(gif_key, ""),
                image=matched.get(image_key, ""),
                type=exercise_type,
                has_next=has_next,
                next_exercise=next_exercise_name,
                logged=logged_sets,
            ),
        )

    # ── 4. Report only ──

    async def get_report(self, client_id: int) -> TodayReportResponse:
        resolved_gender = await self._resolve_gender(client_id)
        today = date.today()
        todays_record = await self.repo.get_todays_workout(client_id, today)

        if not todays_record or not todays_record.workout_details:
            return TodayReportResponse(
                gender=resolved_gender,
                report=TodayReport(
                    total_volume=0,
                    total_time_mins=0,
                    exercises_completed=0,
                    total_calories_burnt=0,
                ),
            )

        total_volume = 0.0
        total_calories = 0.0
        total_time = 0.0
        exercises_done = set()

        for entry in todays_record.workout_details:
            for group_name, exercise_list in entry.items():
                for exercise in exercise_list:
                    exercises_done.add(exercise.get("name", ""))
                    for s in exercise.get("sets", []):
                        total_volume += float(s.get("reps", 0)) * float(s.get("weight", 0))
                        try:
                            total_calories += float(s.get("calories", 0) or 0)
                        except (ValueError, TypeError):
                            pass
                        total_time += float(s.get("duration", 0) or 0)

        total_time_mins = round(total_time / 60, 1) if total_time else 0
        if total_time_mins == 0 and todays_record.workout_time:
            total_time_mins = float(todays_record.workout_time)

        # Count total exercises across all muscle groups done today
        total_sets_count = 0
        for entry in todays_record.workout_details:
            for exercise_list in entry.values():
                total_sets_count += len(exercise_list)

        report = TodayReport(
            total_volume=round(total_volume, 1),
            total_time_mins=total_time_mins,
            exercises_completed=len(exercises_done),
            total_calories_burnt=round(total_calories, 2),
        )

        return TodayReportResponse(gender=resolved_gender, report=report)

    # ── 5. Workout history ──

    async def get_history(
        self, client_id: int, workout_date: date
    ) -> WorkoutHistoryResponse:
        record = await self.repo.get_workout_by_date(client_id, workout_date)

        if not record or not record.workout_details:
            return WorkoutHistoryResponse(
                date=workout_date.isoformat(), 
                report=DayReport(
                    total_volume=0,
                    total_time_mins=0,
                    exercises_completed=0,
                    total_calories_burnt=0,
                ),
                data=[],
            )

        total_volume = 0.0
        total_calories = 0.0
        total_time = 0.0
        exercises_done = set()
        muscle_map: Dict[str, List[LoggedExerciseItem]] = {}

        for entry in record.workout_details:
            for group_name, exercise_list in entry.items():
                for exercise in exercise_list:
                    ex_name = exercise.get("name", "")
                    exercises_done.add(ex_name)
                    sets = []
                    for s in exercise.get("sets", []):
                        reps = int(s.get("reps", 0))
                        weight = float(s.get("weight", 0))
                        total_volume += reps * weight
                        try:
                            cal = float(s.get("calories", 0) or 0)
                            total_calories += cal
                        except (ValueError, TypeError):
                            cal = 0.0
                        dur = float(s.get("duration", 0) or 0)
                        total_time += dur
                        sets.append(LoggedSetItem(
                            set_number=s.get("setNumber", 0),
                            reps=reps,
                            weight=weight,
                            calories=cal,
                            duration=dur,
                        ))

                    logged_ex = LoggedExerciseItem(name=ex_name, sets=sets)
                    if group_name in muscle_map:
                        muscle_map[group_name].append(logged_ex)
                    else:
                        muscle_map[group_name] = [logged_ex]

        total_time_mins = round(total_time / 60, 1) if total_time else 0
        if total_time_mins == 0 and record.workout_time:
            total_time_mins = float(record.workout_time)

        report = DayReport(
            total_volume=round(total_volume, 1),
            total_time_mins=total_time_mins,
            exercises_completed=len(exercises_done),
            total_calories_burnt=round(total_calories, 2),
        )

        data = [
            HistoryMuscleGroup(muscle_group=group, exercises=exs)
            for group, exs in muscle_map.items()
        ]

        return WorkoutHistoryResponse(
            date=workout_date.isoformat(),
            report=report,
            data=data,
        )

    # ── Private helpers ──

    @staticmethod
    def _extract_logged_exercises(
        record, muscle_group: str
    ) -> Dict[str, List[LoggedSetItem]]:
        """Extract logged exercises for a muscle group from today's actual_workout."""
        logged: Dict[str, List[LoggedSetItem]] = {}
        if not record or not record.workout_details:
            return logged

        for entry in record.workout_details:
            if muscle_group not in entry:
                continue
            for exercise in entry[muscle_group]:
                name = exercise.get("name", "")
                sets = []
                for s in exercise.get("sets", []):
                    sets.append(LoggedSetItem(
                        set_number=s.get("setNumber", 0),
                        reps=int(s.get("reps", 0)),
                        weight=float(s.get("weight", 0)),
                        calories=float(s.get("calories", 0) or 0),
                        duration=float(s.get("duration", 0) or 0),
                    ))
                if name in logged:
                    logged[name].extend(sets)
                else:
                    logged[name] = sets
        return logged

    async def _build_report(
        self,
        client_id: int,
        muscle_group: str,
        logged_map: Dict[str, List[LoggedSetItem]],
        todays_record,
        total_exercises: int,
        today: date,
    ) -> MuscleGroupReport:
        total_volume = 0.0
        total_calories = 0.0
        total_time = 0.0

        if todays_record and todays_record.workout_details:
            for entry in todays_record.workout_details:
                if muscle_group not in entry:
                    continue
                for exercise in entry[muscle_group]:
                    for s in exercise.get("sets", []):
                        reps = float(s.get("reps", 0))
                        weight = float(s.get("weight", 0))
                        total_volume += reps * weight
                        try:
                            total_calories += float(s.get("calories", 0) or 0)
                        except (ValueError, TypeError):
                            pass
                        total_time += float(s.get("duration", 0) or 0)

        total_time_mins = round(total_time / 60, 1) if total_time else 0

        if total_time_mins == 0 and todays_record and todays_record.workout_time:
            total_time_mins = float(todays_record.workout_time)

        exercises_completed = len(logged_map)

        prev_exercises = await self.repo.get_previous_muscle_group_workout(
            client_id, muscle_group, today
        )
        volume_change_pct = None
        if prev_exercises:
            prev_volume = 0.0
            for exercise in prev_exercises:
                for s in exercise.get("sets", []):
                    prev_volume += float(s.get("reps", 0)) * float(s.get("weight", 0))
            if prev_volume > 0:
                volume_change_pct = round(((total_volume - prev_volume) / prev_volume) * 100, 1)

        return MuscleGroupReport(
            total_volume=round(total_volume, 1),
            volume_change_pct=volume_change_pct,
            total_time_mins=total_time_mins,
            exercises_completed=exercises_completed,
            exercises_total=total_exercises,
            total_calories_burnt=round(total_calories, 2),
        )

    # ── Streak ──

    async def _build_streak(self, client_id: int, today: date) -> List[StreakDay]:
        logged_dates = await self.repo.get_last_7_days_dates(client_id, today)
        streak = []
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            streak.append(StreakDay(
                date=d.isoformat(),
                logged=d in logged_dates,
            ))
        return streak

    # ── Suggested muscle groups ──

    async def _build_suggestions(
        self, client_id: int, current_muscle_group: str, today: date, gender: str
    ) -> tuple:
        """
        Suggest muscle groups for tomorrow and day after tomorrow.

        Logic:
        1. Fetch last 21 days of workouts
        2. Extract muscle groups per day (normalize to canonical names)
        3. Detect user's split pattern — the order they rotate through muscle groups
        4. Suggest the next groups in their rotation that they haven't done recently
        5. Never repeat today's muscle group for tomorrow
        6. If no history, fall back to Push/Pull/Legs rotation
        """
        recent_workouts = await self.repo.get_recent_workouts(client_id, today)

        # Build ordered list: which muscle groups trained on which days (most recent first)
        # Also track last-trained date per muscle group
        last_trained: Dict[str, date] = {}
        day_groups: List[List[str]] = []  # per-day muscle groups

        for workout in recent_workouts:
            if not workout.workout_details:
                continue
            groups_today = []
            for entry in workout.workout_details:
                for group_name in entry.keys():
                    normalized = self._normalize_group(group_name)
                    if normalized and normalized not in groups_today:
                        groups_today.append(normalized)
                    if normalized and normalized not in last_trained:
                        last_trained[normalized] = workout.date
            if groups_today:
                day_groups.append(groups_today)

        # Detect split: extract the user's rotation order from their history
        user_rotation = self._detect_rotation(day_groups)

        # Today's groups to exclude from tomorrow
        todays_groups = set()
        if day_groups:
            # Check if the most recent workout is today
            if recent_workouts and recent_workouts[0].date == today:
                todays_groups = set(day_groups[0])
        todays_groups.add(self._normalize_group(current_muscle_group) or current_muscle_group)

        # Build muscle group → image map
        muscle_rows = await self.repo.get_muscle_groups_by_gender(gender)
        image_map = {row.muscle_group: row.url for row in muscle_rows}

        # Pick suggestions: next in rotation that wasn't done today
        tmw, dat = self._pick_next_two(user_rotation, todays_groups, last_trained, today, image_map)

        return tmw, dat

    @staticmethod
    def _normalize_group(name: str) -> Optional[str]:
        """Normalize muscle group names to canonical form."""
        mapping = {
            "abs": "ABS",
            "leg": "Leg",
            "legs": "Leg",
            "back": "Back",
            "chest": "Chest",
            "biceps": "Biceps",
            "triceps": "Triceps",
            "shoulder": "Shoulder",
            "shoulders": "Shoulder",
            "forearms": "Forearms",
            "forearm": "Forearms",
            "core": "Core",
            "cardio": "Cardio",
            "cycling": "Cycling",
            "treadmill": "Treadmill",
        }
        return mapping.get(name.lower(), name)

    @staticmethod
    def _detect_rotation(day_groups: List[List[str]]) -> List[str]:
        """
        Detect user's split rotation from their workout history.
        Returns ordered list of muscle groups in the order user tends to cycle through.
        Most recently trained last (so we suggest starting from the end).
        """
        if not day_groups:
            return list(STRENGTH_ROTATION)

        # Build rotation: unique groups in the order they appear across days (oldest first)
        seen = set()
        rotation = []
        for groups in reversed(day_groups):  # oldest first
            for g in groups:
                if g not in seen and g in set(STRENGTH_ROTATION):
                    seen.add(g)
                    rotation.append(g)

        # Add any STRENGTH_ROTATION groups the user hasn't done
        for g in STRENGTH_ROTATION:
            if g not in seen:
                rotation.append(g)

        return rotation

    @staticmethod
    def _pick_next_two(
        rotation: List[str],
        todays_groups: set,
        last_trained: Dict[str, date],
        today: date,
        image_map: Dict[str, str],
    ) -> tuple:
        """Pick the next two muscle groups from rotation, prioritizing longest rest."""

        def sort_key(group: str) -> int:
            if group in last_trained:
                return (today - last_trained[group]).days
            return 999  # never trained = suggest first

        sorted_groups = sorted(rotation, key=sort_key, reverse=True)

        tmw_group = None
        dat_group = None

        for g in sorted_groups:
            if g in todays_groups:
                continue
            if tmw_group is None:
                tmw_group = g
            elif dat_group is None:
                dat_group = g
                break

        if not tmw_group:
            tmw_group = DEFAULT_SUGGESTIONS[0][0]
        if not dat_group:
            dat_group = DEFAULT_SUGGESTIONS[1][0]

        tmw_reason = FittbotWorkoutService._build_reason(tmw_group, last_trained, today)
        dat_reason = FittbotWorkoutService._build_reason(dat_group, last_trained, today)

        return (
            SuggestedMuscleGroup(muscle_group=tmw_group, image=image_map.get(tmw_group, ""), reason=tmw_reason),
            SuggestedMuscleGroup(muscle_group=dat_group, image=image_map.get(dat_group, ""), reason=dat_reason),
        )

    @staticmethod
    def _build_reason(group: str, last_trained: Dict[str, date], today: date) -> str:
        if group not in last_trained:
            return f"You haven't trained {group} recently"
        days_ago = (today - last_trained[group]).days
        if days_ago == 0:
            return f"Continue your {group} split"
        if days_ago == 1:
            return f"Last trained {group} yesterday"
        return f"Last trained {group} {days_ago} days ago"
