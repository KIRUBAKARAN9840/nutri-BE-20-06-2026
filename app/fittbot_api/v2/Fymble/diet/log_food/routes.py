"""Thin FastAPI endpoints for Log Food.

No business logic here — delegates everything to the service layer.
"""

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.redis_config import get_redis
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions

from .schemas import (
    LogFoodRequest,
    LogFoodResponse,
    LogScannedFoodRequest,
    LogScannedFoodResponse,
)
from .service import LogFoodService

router = APIRouter(prefix="/log_food", tags=["LogFood V2"])


@router.post("/add", response_model=LogFoodResponse)
@log_exceptions
async def add_food(
    req: LogFoodRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = LogFoodService(db, redis)
    return await service.add_food(client_id, req)


@router.post("/add_scanned", response_model=LogScannedFoodResponse)
@log_exceptions
async def add_scanned_food(
    req: LogScannedFoodRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = LogFoodService(db, redis)
    return await service.add_scanned_food(client_id, req)


