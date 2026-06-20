"""Thin FastAPI endpoint for Gym Membership Bookings (checkout preview).

No business logic here — delegates everything to the service layer.
"""

from fastapi import APIRouter, Depends, Query, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.redis_config import get_redis
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions

from .schemas import ApplyCouponRequest, MembershipBookingResponse
from .service import MembershipBookingsService

router = APIRouter(prefix="/gym_membership_bookings", tags=["GymMembershipBookings V2"])


@router.get("/data", response_model=MembershipBookingResponse)
@log_exceptions
async def membership_booking_data(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
    gym_id: int = Query(...),
    plan_id: int = Query(...),
):
    service = MembershipBookingsService(db, redis)
    result = await service.calculate_pricing(
        client_id=client_id,
        gym_id=gym_id,
        plan_id=plan_id,
    )

    return result


@router.post("/apply_coupon", response_model=MembershipBookingResponse)
@log_exceptions
async def apply_coupon(
    request: Request,
    body: ApplyCouponRequest,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    """Validate coupon (by code + client_id only) and return full pricing with discount."""
    service = MembershipBookingsService(db, redis)
    return await service.calculate_pricing(
        client_id=client_id,
        gym_id=body.gym_id,
        plan_id=body.plan_id,
        coupon_code=body.coupon_code,
    )


