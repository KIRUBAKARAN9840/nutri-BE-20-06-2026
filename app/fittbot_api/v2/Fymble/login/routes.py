

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions
from app.utils.redis_config import get_redis
from app.utils.security import attach_auth_cookies

from .schemas import (
    LoginRequest,
    OTPVerifyRequest,
    RegisterRequest,
    ResendOTPRequest,
)
from .service import LoginService

router = APIRouter(prefix="/client/new_registration", tags=["Client Auth V2"])


def _get_service(
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
) -> LoginService:
    return LoginService(db, redis)


def _client_ip(request: Request) -> str:
    """Extract client IP -- handles X-Forwarded-For behind load balancer."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _user_agent(request: Request) -> str:
    return (request.headers.get("user-agent") or "unknown")[:512]


# ── Login (public) ───────────────────────────────────────────────────────────

@router.post("/login")
@log_exceptions
async def client_login(
    request: Request,
    body: LoginRequest,
    svc: LoginService = Depends(_get_service),
):
    return await svc.login(
        body.mobile_number,
        client_ip=_client_ip(request),
        user_agent=_user_agent(request),
    )


# ── OTP Verification (public) ───────────────────────────────────────────────

@router.post("/verify-otp")
@log_exceptions
async def verify_otp(
    request: Request,
    body: OTPVerifyRequest,
    svc: LoginService = Depends(_get_service),
):
    return await svc.verify_otp(
        body.data, body.otp,
        client_ip=_client_ip(request),
        user_agent=_user_agent(request),
    )


# ── Resend OTP (public) ─────────────────────────────────────────────────────

@router.post("/resend-otp")
@log_exceptions
async def resend_otp(
    request: Request,
    body: ResendOTPRequest,
    svc: LoginService = Depends(_get_service),
):
    return await svc.resend_otp(
        body.data,
        client_ip=_client_ip(request),
        user_agent=_user_agent(request),
    )


# ── Registration (authenticated) ────────────────────────────────────────────

@router.post("/register")
@log_exceptions
async def register_client(
    request: Request,
    body: RegisterRequest,
    svc: LoginService = Depends(_get_service),
):
    result = await svc.register(
        name=body.name,
        mobile=body.mobile_number,
        gender=body.gender,
        location=body.location,
        referral_id=body.referral_id,
        platform=body.platform,
        is_from_ad=bool(body.is_from_ad),
        client_ip=_client_ip(request),
        user_agent=_user_agent(request),
    )

    if body.is_from_ad:
        data = result.get("data") or {}
        access_token = data.get("access_token")
        refresh_token = data.get("refresh_token")
        if access_token and refresh_token:
            response = JSONResponse(content=result)
            attach_auth_cookies(response, access_token, refresh_token)
            return response

    return result


# ── Refresh Token Rotation (authenticated) ──────────────────────────────────

@router.post("/refresh")
@log_exceptions
async def refresh_token(
    request: Request,
    svc: LoginService = Depends(_get_service),
    client_id: int = Depends(get_verified_client_id),
):
    # Extract refresh token from Authorization header or body
    auth_header = request.headers.get("x-refresh-token", "")
    if not auth_header:
        from app.utils.logging_utils import FittbotHTTPException
        raise FittbotHTTPException(
            status_code=400,
            detail="x-refresh-token header required",
            error_code="MISSING_REFRESH_TOKEN",
        )

    return await svc.rotate_refresh_token(
        client_id=client_id,
        old_refresh_token=auth_header,
        client_ip=_client_ip(request),
        user_agent=_user_agent(request),
    )
