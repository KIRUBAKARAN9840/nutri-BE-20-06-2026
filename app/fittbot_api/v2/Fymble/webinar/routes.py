from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions
from app.utils.redis_config import get_redis

from .schemas import (
    WebinarRegisterRequest,
    WebinarRegisterResponse,
)
from .service import WebinarService

router = APIRouter(prefix="/webinar", tags=["Fymble Webinar"])


def _get_service(
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
) -> WebinarService:
    return WebinarService(db, redis)


@router.post("/register", response_model=WebinarRegisterResponse)
@log_exceptions
async def register_webinar(
    body: WebinarRegisterRequest,
    svc: WebinarService = Depends(_get_service),
) -> WebinarRegisterResponse:
    webinar_id, is_update = await svc.register(
        name=body.name,
        mobile_number=body.mobile_number,
        gender=body.gender,
        location=body.location,
        aim=body.aim,
    )
    return WebinarRegisterResponse(
        message="Registration updated" if is_update else "Registration saved",
        webinar_id=webinar_id,
        is_update=is_update,
    )


@router.post("/app_register", response_model=WebinarRegisterResponse)
@log_exceptions
async def register_webinar_from_app(
    svc: WebinarService = Depends(_get_service),
    client_id: int = Depends(get_verified_client_id),
) -> WebinarRegisterResponse:
    webinar_id, is_update = await svc.register_from_app(
        client_id=client_id,
        aim= "",
    )
    return WebinarRegisterResponse(
        message="Registration updated" if is_update else "Registration saved",
        webinar_id=webinar_id,
        is_update=is_update,
    )
