from fastapi import APIRouter, Depends, Query, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions
from app.utils.redis_config import get_redis

from .api import HomeAPI, build_home_api
from ._http_schemas import GetHomeResponse


router = APIRouter(prefix="/gym_mate/home", tags=["GymMate Home V2"])


def _api(
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
) -> HomeAPI:
    return build_home_api(db, redis)


@router.get("", response_model=GetHomeResponse)
@log_exceptions
async def get_home(
    request: Request,
    lat: float = Query(..., ge=-90, le=90, description="Client latitude"),
    lng: float = Query(..., ge=-180, le=180, description="Client longitude"),
    client_id: int = Depends(get_verified_client_id),
    api: HomeAPI = Depends(_api),
):

    payload = await api.get_home(
        client_id=client_id,
        lat=lat,
        lng=lng,
    )
    return GetHomeResponse(data=payload)
