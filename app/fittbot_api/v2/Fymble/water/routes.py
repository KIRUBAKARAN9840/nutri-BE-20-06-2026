
from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.async_database import get_async_db
from app.utils.redis_config import get_redis
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions

from .schemas import (
    AddWaterResponse,
    SetWaterTargetRequest,
    SetWaterTargetResponse,
    SetWaterReminderRequest,
    SetWaterReminderResponse,
    DeleteWaterReminderResponse,
    GetWaterResponse,
)
from .service import WaterService

router = APIRouter(prefix="/water", tags=["Water Tracker V2"])


@router.get("/get", response_model=GetWaterResponse)
@log_exceptions
async def get_water(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = WaterService(db, redis)
    return await service.get_water(client_id)


@router.post("/add", response_model=AddWaterResponse)
@log_exceptions
async def add_water(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = WaterService(db, redis)
    return await service.add_water(client_id)


@router.post("/set_target", response_model=SetWaterTargetResponse)
@log_exceptions
async def set_water_target(
    req: SetWaterTargetRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = WaterService(db, redis)
    return await service.set_target(client_id, req)


@router.post("/set_reminder", response_model=SetWaterReminderResponse)
@log_exceptions
async def set_water_reminder(
    req: SetWaterReminderRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = WaterService(db, redis)
    return await service.set_reminder(client_id, req)


@router.delete("/delete_reminder", response_model=DeleteWaterReminderResponse)
@log_exceptions
async def delete_water_reminder(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = WaterService(db, redis)
    return await service.delete_reminder(client_id)
