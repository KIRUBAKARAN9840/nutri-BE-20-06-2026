"""Thin FastAPI endpoints for Fittbot Workout.

No business logic here — delegates everything to the service layer.
"""

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions
from app.utils.redis_config import get_redis

from datetime import date

from .schemas import (
    ExerciseDetailResponse,
    ExerciseListResponse,
    FittbotWorkoutResponse,
    TodayReportResponse,
    WorkoutHistoryResponse,
)
from .service import FittbotWorkoutService

router = APIRouter(prefix="/fittbot-workout", tags=["Workout — Fittbot V2"])


@router.get("/muscle-groups", response_model=FittbotWorkoutResponse)
@log_exceptions
async def get_muscle_groups(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = FittbotWorkoutService(db, redis)
    return await service.get_muscle_groups(client_id)


@router.get("/exercises", response_model=ExerciseListResponse)
@log_exceptions
async def get_exercises(
    request: Request,
    muscle_group: str,
    category: str = "gym",
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = FittbotWorkoutService(db, redis)
    return await service.get_exercises(client_id, muscle_group, category)


@router.get("/exercise-detail", response_model=ExerciseDetailResponse)
@log_exceptions
async def get_exercise_detail(
    request: Request,
    muscle_group: str,
    exercise_id: int,
    category: str = "gym",
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = FittbotWorkoutService(db, redis)
    return await service.get_exercise_detail(client_id, muscle_group, exercise_id, category)


@router.get("/report", response_model=TodayReportResponse)
@log_exceptions
async def get_report(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = FittbotWorkoutService(db, redis)
    return await service.get_report(client_id)


@router.get("/history", response_model=WorkoutHistoryResponse)
@log_exceptions
async def get_history(
    request: Request,
    workout_date: date,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = FittbotWorkoutService(db, redis)
    return await service.get_history(client_id, workout_date)
