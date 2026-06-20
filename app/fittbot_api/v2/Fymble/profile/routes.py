"""HTTP routes for the v2 Profile module.

Routes are thin: they parse the request, hand it to ProfileService, and
return the response model. All validation, OTP, and DB logic lives in
the service / repository layers.

client_id always comes from `get_verified_client_id` (JWT) — never from
the request body or query string. This prevents IDOR.
"""

from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions
from app.utils.redis_config import get_redis

from .schemas import (
    InitiateContactChangePayload,
    InitiateContactChangeResponse,
    ProfileResponse,
    UpdateDetailsPayload,
    UpdateDetailsResponse,
    VerifyContactChangePayload,
    VerifyContactChangeResponse,
)
from .service import ProfileService

router = APIRouter(prefix="/profile", tags=["Profile V2"])


# ── 1. GET /profile/data ─────────────────────────────────────────────


@router.get("/data", response_model=ProfileResponse)
@log_exceptions
async def get_profile_data(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = ProfileService(db, redis)
    return await service.get_profile(client_id)


# ── 2. PUT /profile/details ──────────────────────────────────────────


@router.put("/details", response_model=UpdateDetailsResponse)
@log_exceptions
async def update_profile_details(
    request: Request,
    payload: UpdateDetailsPayload,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = ProfileService(db, redis)
    return await service.update_details(client_id, payload)


# ── 3a. POST /profile/contact/initiate ───────────────────────────────


@router.post("/contact/initiate", response_model=InitiateContactChangeResponse)
@log_exceptions
async def initiate_contact_change(
    request: Request,
    payload: InitiateContactChangePayload,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = ProfileService(db, redis)
    client_ip = request.client.host if request.client else None
    return await service.initiate_contact_change(
        client_id=client_id,
        new_contact=payload.new_contact,
        client_ip=client_ip,
    )


# ── 3b. POST /profile/contact/verify ─────────────────────────────────


@router.post("/contact/verify", response_model=VerifyContactChangeResponse)
@log_exceptions
async def verify_contact_change(
    request: Request,
    payload: VerifyContactChangePayload,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = ProfileService(db, redis)
    return await service.verify_contact_change(
        client_id=client_id,
        old_otp=payload.old_otp,
        new_otp=payload.new_otp,
    )
