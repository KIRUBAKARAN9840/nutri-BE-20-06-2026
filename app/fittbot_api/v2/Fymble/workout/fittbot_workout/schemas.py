"""Pydantic request/response models for Fittbot Workout."""

from typing import List, Optional
from pydantic import BaseModel


# ── Muscle Groups ────────────────────────────────────────────────────


class MuscleGroupItem(BaseModel):
    """A single muscle group with its gender-specific image."""

    id: int
    muscle_group: str
    image: str


class FittbotWorkoutResponse(BaseModel):
    """Response for GET /fittbot-workout/muscle-groups."""

    status: int = 200
    data: List[MuscleGroupItem]


# ── Exercise List ────────────────────────────────────────────────────


class LoggedSetItem(BaseModel):
    """A single logged set with reps and weight."""

    set_number: int
    reps: int
    weight: float
    calories: float
    duration: float  # in seconds


class LoggedExerciseItem(BaseModel):
    """An exercise that was logged today with its sets."""

    name: str
    sets: List[LoggedSetItem]


class ExerciseItem(BaseModel):
    """A single exercise with gender-specific image."""

    id: int
    name: str
    image: str
    type: str  # strength, cardio, body_weight
    logged: Optional[List[LoggedSetItem]] = None


class MuscleGroupReport(BaseModel):
    """Today's report for this muscle group."""

    total_volume: float
    volume_change_pct: Optional[float] = None
    total_time_mins: float
    exercises_completed: int
    exercises_total: int
    total_calories_burnt: float


class TodayReport(BaseModel):
    """Overall report for today across all muscle groups."""

    total_volume: float
    total_time_mins: float
    exercises_completed: int
    total_calories_burnt: float


class TodayReportResponse(BaseModel):
    """Response for GET /fittbot-workout/report."""

    status: int = 200
    gender: str
    report: TodayReport


class StreakDay(BaseModel):
    """A single day in the 7-day streak."""

    date: str
    logged: bool


class SuggestedMuscleGroup(BaseModel):
    """Suggested muscle group for a future day."""

    muscle_group: str
    image: str
    reason: str


class ExerciseListResponse(BaseModel):
    """Response for GET /fittbot-workout/exercises."""

    status: int = 200
    muscle_group: str
    is_cardio: bool
    report: Optional[MuscleGroupReport] = None
    streak: List[StreakDay]
    suggested_tomorrow: SuggestedMuscleGroup
    suggested_day_after: SuggestedMuscleGroup
    data: List[ExerciseItem]


# ── Exercise Detail ──────────────────────────────────────────────────


class ExerciseDetailData(BaseModel):
    """Single exercise detail with gif and image."""

    id: int
    name: str
    gif: str
    image: str
    type: str  # strength, cardio, body_weight
    has_next: bool
    next_exercise: Optional[str] = None
    logged: Optional[List[LoggedSetItem]] = None


class ExerciseDetailResponse(BaseModel):
    """Response for GET /fittbot-workout/exercise-detail."""

    status: int = 200
    muscle_group: str
    data: ExerciseDetailData


# ── Workout History ──────────────────────────────────────────────────


class HistoryMuscleGroup(BaseModel):
    """A muscle group's exercises done on a specific day."""

    muscle_group: str
    exercises: List[LoggedExerciseItem]


class DayReport(BaseModel):
    """Report for a single day."""

    total_volume: float
    total_time_mins: float
    exercises_completed: int
    total_calories_burnt: float


class WorkoutHistoryDay(BaseModel):
    """A single day's workout data."""

    date: str
    report: DayReport
    data: List[HistoryMuscleGroup]


class WorkoutHistoryResponse(BaseModel):
    """Response for GET /fittbot-workout/history."""

    status: int = 200
    date: str
    report: DayReport
    data: List[HistoryMuscleGroup]
