"""Thin FastAPI endpoint for logging workouts.

No business logic here — delegates everything to the service layer.
"""

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions
from app.utils.redis_config import get_redis

from .schemas import LogWorkoutRequest, LogWorkoutResponse, RemoveSetRequest, RemoveSetResponse
from .service import LogWorkoutService

router = APIRouter(prefix="/log-workout", tags=["Workout — Log V2"])


@router.post("/add", response_model=LogWorkoutResponse)
@log_exceptions
async def add_workout(
    request: Request,
    data: LogWorkoutRequest,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = LogWorkoutService(db, redis)
    return await service.log_workout(client_id, data)


@router.post("/remove_set", response_model=RemoveSetResponse)
@log_exceptions
async def remove_set(
    request: Request,
    data: RemoveSetRequest,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = LogWorkoutService(db, redis)
    return await service.remove_set(client_id, data)

