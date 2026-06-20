"""Pydantic request/response models for logging workouts."""

from datetime import date
from typing import List, Optional

from pydantic import BaseModel


# ── Request ──────────────────────────────────────────────────────────


class LogWorkoutRequest(BaseModel):
    """Body for POST /log-workout/add."""

    workout_details: list
    workout_time: Optional[float] = None  # in minutes


class RemoveSetRequest(BaseModel):
    """Body for DELETE /log-workout/remove-set."""

    set_id: str


# ── Response ─────────────────────────────────────────────────────────


class LogWorkoutResponse(BaseModel):
    """Response for POST /log-workout/add."""

    status: int = 200
    message: str
    record_id: int
    total_burnt_calories: float
    xp_earned: int
    feedback: bool
    set_ids: List[str] = []


class RemoveSetResponse(BaseModel):
    """Response for DELETE /log-workout/remove-set."""

    status: int = 200
    message: str
    calories_removed: float
