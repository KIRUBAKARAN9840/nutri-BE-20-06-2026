"""Thin FastAPI endpoint for Daily Pass active bookings.

No business logic here — delegates everything to the service layer.
"""

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions
from app.utils.redis_config import get_redis

from .schemas import DailyPassListResponse
from .service import DailyPassBookingService

router = APIRouter(prefix="/dailypass", tags=["Bookings — Daily Pass V2"])


@router.get("/all", response_model=DailyPassListResponse)
@log_exceptions
async def list_active_passes(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = DailyPassBookingService(db, redis)
    return await service.list_active(client_id)
