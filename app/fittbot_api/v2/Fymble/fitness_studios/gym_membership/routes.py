"""Thin FastAPI endpoints for Gym Membership listing + details.

No business logic here -- delegates everything to the service layer.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.redis_config import get_redis
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions

from app.config.pricing import get_walkaway_visited_key, get_seconds_until_midnight_ist

from .schemas import GymDetailsResponse, MembershipListParams, MembershipListResponse
from .service import MembershipService

router = APIRouter(prefix="/gym_membership", tags=["GymMembership V2"])


@router.get("/gyms", response_model=MembershipListResponse)
@log_exceptions
async def list_membership_gyms(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
    search: Optional[str] = None,
    city: Optional[str] = None,
    area: Optional[str] = None,
    pincode: Optional[str] = None,
    state: Optional[str] = None,
    client_lat: Optional[float] = None,
    client_lng: Optional[float] = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=10, ge=1, le=50),
    sort_price: Optional[bool] = False,
    sort_type: Optional[str] = "ascending",
    fitness_types: Optional[List[str]] = Query(default=None),
    no_cost_emi: Optional[bool] = None,
    membership_types: Optional[List[str]] = Query(default=None),
):

    params = MembershipListParams(
        search=search,
        city=city,
        area=area,
        pincode=pincode,
        state=state,
        client_lat=client_lat,
        client_lng=client_lng,
        page=page,
        limit=limit,
        client_id=client_id,
        sort_price=sort_price,
        sort_type=sort_type,
        fitness_types=fitness_types,
        no_cost_emi=no_cost_emi,
        membership_types=membership_types,
    )

    service = MembershipService(db, redis)
    return await service.list_gyms(params, client_id)


@router.get("/gym_details", response_model=GymDetailsResponse)
@log_exceptions
async def get_gym_details(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
    gym_id: int = Query(...),
):

    service = MembershipService(db, redis)

    result = await service.get_gym_details(gym_id, client_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Gym not found")

    # Mark that user visited a gym details page today (SET NX — first visit sets it)
    # This is NOT the discount key — discount activates only when user returns to listing
    visited_key = get_walkaway_visited_key(client_id)
    ttl = get_seconds_until_midnight_ist()
    await redis.set(visited_key, "1", nx=True, ex=ttl)

    from app.services.activity_tracker import track_event
    await track_event(client_id, "membership_viewed", gym_id=gym_id, product_type="membership", source="v2_gym_membership")

    return result
