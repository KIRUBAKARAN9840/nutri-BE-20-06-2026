"""Thin FastAPI endpoint for Personal Training Bookings (checkout preview).

No business logic here — delegates everything to the service layer.
"""

from typing import List

from fastapi import APIRouter, Depends, Query, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.redis_config import get_redis
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions

from .schemas import PTBookingResponse
from .service import PTBookingsService

router = APIRouter(prefix="/personal_training_bookings", tags=["PT Bookings V2"])


@router.get("/data", response_model=PTBookingResponse)
@log_exceptions
async def pt_booking_data(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
    gym_id: int = Query(...),
    trainer_id: int = Query(...),
    schedule_id: int = Query(...),
    dates: List[str] = Query(..., description="Selected dates in YYYY-MM-DD format"),
):
    service = PTBookingsService(db, redis)
    result = await service.calculate_pricing(
        client_id=client_id,
        gym_id=gym_id,
        trainer_id=trainer_id,
        schedule_id=schedule_id,
        dates=dates,
    )

    return result
