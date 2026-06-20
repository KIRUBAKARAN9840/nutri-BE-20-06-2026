"""Thin FastAPI endpoint for Session Bookings (checkout preview).

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

from .schemas import SessionBookingResponse
from .service import SessionBookingsService

router = APIRouter(prefix="/session_bookings", tags=["SessionBookings V2"])


@router.get("/data", response_model=SessionBookingResponse)
@log_exceptions
async def session_booking_data(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
    gym_id: int = Query(...),
    session_id: int = Query(...),
    schedule_id: int = Query(...),
    dates: List[str] = Query(..., description="Selected dates in YYYY-MM-DD format"),
):
    service = SessionBookingsService(db, redis)
    result = await service.calculate_pricing(
        client_id=client_id,
        gym_id=gym_id,
        session_id=session_id,
        schedule_id=schedule_id,
        dates=dates,
    )

    return result
