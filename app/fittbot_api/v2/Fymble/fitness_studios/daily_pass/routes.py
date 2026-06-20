"""Thin FastAPI endpoints for Daily Pass gym listing.

No business logic here — delegates everything to the service layer.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.redis_config import get_redis
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions

from .schemas import DailyPassListParams, DailyPassListResponse, GymDetailsResponse
from .service import DailyPassService

router = APIRouter(prefix="/daily_pass", tags=["DailyPass V2"])


@router.get("/gyms", response_model=DailyPassListResponse)
@log_exceptions
async def list_dailypass_gyms(
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
    dailypass_low: Optional[bool] = False,
):
    
    params = DailyPassListParams(
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
        dailypass_low=dailypass_low,
    )

    service = DailyPassService(db, redis)
    return await service.list_gyms(params)


@router.get("/gym_details", response_model=GymDetailsResponse)
@log_exceptions
async def get_gym_details(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
    gym_id: int = Query(...),
):

    service = DailyPassService(db, redis)
    result = await service.get_gym_details(gym_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Gym not found")

    from app.services.activity_tracker import track_event
    await track_event(client_id, "dailypass_viewed", gym_id=gym_id, product_type="dailypass", source="v2_daily_pass")
    
    return result


