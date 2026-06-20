"""Thin FastAPI endpoints for Personal Training gym listing.

No business logic here — delegates everything to the service layer.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.redis_config import get_redis
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions

from .schemas import PTListParams, PTListResponse, PTTrainerListResponse, PTTrainerSlotsResponse
from .service import PTService

router = APIRouter(prefix="/personal_training", tags=["Personal Training V2"])


@router.get("/gyms", response_model=PTListResponse)
@log_exceptions
async def list_pt_gyms(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
    dates: List[str] = Query(..., description="Target dates in YYYY-MM-DD format"),
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
    session_low: Optional[bool] = False,
):

    params = PTListParams(
        dates=dates,
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
        session_low=session_low,
    )

    service = PTService(db, redis)
    return await service.list_gyms(params)


@router.get("/trainers", response_model=PTTrainerListResponse)
@log_exceptions
async def list_pt_trainers(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    gym_id: int = Query(..., description="Gym ID to list trainers for"),
    exclude_trainer_id: int = Query(..., description="Primary trainer ID to exclude"),
):
    service = PTService(db, redis)
    return await service.get_trainers(gym_id, exclude_trainer_id)


@router.get("/trainer_slots", response_model=PTTrainerSlotsResponse)
@log_exceptions
async def get_pt_trainer_slots(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
    gym_id: int = Query(..., description="Gym ID"),
    trainer_id: int = Query(..., description="Trainer ID to get slots for"),
    dates: List[str] = Query(..., description="Target dates in YYYY-MM-DD format"),
):
    service = PTService(db, redis)
    result = await service.get_trainer_slots(gym_id, trainer_id, dates, client_id)

    from app.services.activity_tracker import track_event
    await track_event(
        client_id, "session_viewed",
        gym_id=gym_id,
        product_type="session",
        product_details={"trainer_id": trainer_id, "dates": dates},
        source="v2_personal_training",
    )

    return result
