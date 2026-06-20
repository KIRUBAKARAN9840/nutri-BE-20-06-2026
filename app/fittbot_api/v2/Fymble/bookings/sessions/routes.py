"""Thin FastAPI endpoint for Session upcoming bookings.

No business logic here — delegates everything to the service layer.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions

from .schemas import SessionUpcomingResponse
from .service import SessionBookingService

router = APIRouter(prefix="/sessions", tags=["Bookings — Sessions V2"])


@router.get("/upcoming", response_model=SessionUpcomingResponse)
@log_exceptions
async def get_upcoming_sessions(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
):
    service = SessionBookingService(db)
    return await service.get_upcoming(client_id)
