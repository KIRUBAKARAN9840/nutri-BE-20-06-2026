"""Thin FastAPI endpoints for Session gym listing.

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

from .schemas import SessionListParams, SessionListResponse
from .service import SessionService

router = APIRouter(prefix="/sessions", tags=["Sessions V2"])


@router.get("/get_slots", response_model=SessionListResponse)
@log_exceptions
async def list_session_gyms(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
    session_id: int = Query(..., description="Session type ID to filter by"),
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

    params = SessionListParams(
        session_id=session_id,
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

    service = SessionService(db, redis)
    result = await service.list_gyms(params)

    from app.services.activity_tracker import track_event
    await track_event(
        client_id, "session_viewed",
        product_type="session",
        product_details={"session_id": session_id, "dates": dates},
        source="v2_sessions",
    )

    return result
