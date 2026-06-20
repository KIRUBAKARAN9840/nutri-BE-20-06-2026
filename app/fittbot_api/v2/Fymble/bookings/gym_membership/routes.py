"""Thin FastAPI endpoint for Gym Membership active bookings.

No business logic here — delegates everything to the service layer.
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions

from .schemas import GymMembershipListResponse
from .service import GymMembershipBookingService

router = APIRouter(prefix="/gym_membership", tags=["Bookings — Gym Membership V2"])


@router.get("/all", response_model=GymMembershipListResponse)
@log_exceptions
async def list_active_memberships(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
):
    service = GymMembershipBookingService(db)
    return await service.list_active(client_id)
