

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions
from app.utils.redis_config import get_redis

from .api import WaterAPI, build_water_api
from ._http_schemas import (
    AddWaterResponse,
    DeleteWaterReminderResponse,
    GetWaterResponse,
    SetWaterReminderRequest,
    SetWaterReminderResponse,
    SetWaterTargetRequest,
    SetWaterTargetResponse,
)


router = APIRouter(prefix="/water", tags=["Water Tracker V2"])


def _api(
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
) -> WaterAPI:
    return build_water_api(db, redis)


@router.get("/get", response_model=GetWaterResponse)
@log_exceptions
async def get_water(
    request: Request,
    client_id: int = Depends(get_verified_client_id),
    api: WaterAPI = Depends(_api),
):
    status = await api.get_status(client_id)
    return GetWaterResponse(data=status)


@router.post("/add", response_model=AddWaterResponse)
@log_exceptions
async def add_water(
    request: Request,
    client_id: int = Depends(get_verified_client_id),
    api: WaterAPI = Depends(_api),
):
    await api.log_glass(client_id)
    return AddWaterResponse()


@router.post("/set_target", response_model=SetWaterTargetResponse)
@log_exceptions
async def set_water_target(
    req: SetWaterTargetRequest,
    request: Request,
    client_id: int = Depends(get_verified_client_id),
    api: WaterAPI = Depends(_api),
):
    await api.set_target_litres(client_id, req.target_water)
    return SetWaterTargetResponse()


@router.post("/set_reminder", response_model=SetWaterReminderResponse)
@log_exceptions
async def set_water_reminder(
    req: SetWaterReminderRequest,
    request: Request,
    client_id: int = Depends(get_verified_client_id),
    api: WaterAPI = Depends(_api),
):
    created = await api.set_reminder(
        client_id,
        reminder_type=req.reminder_type,
        is_recurring=req.is_recurring,
        water_timing=req.water_timing,
        intimation_start_time=req.intimation_start_time,
        intimation_end_time=req.intimation_end_time,
    )
    return SetWaterReminderResponse(
        reminder_id=created.reminder_id,
        scheduled_reminder_time=created.scheduled_reminder_time,
    )


@router.delete("/delete_reminder", response_model=DeleteWaterReminderResponse)
@log_exceptions
async def delete_water_reminder(
    request: Request,
    client_id: int = Depends(get_verified_client_id),
    api: WaterAPI = Depends(_api),
):
    await api.delete_reminder(client_id)
    return DeleteWaterReminderResponse()
