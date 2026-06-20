from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.redis_config import get_redis
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions

from .schemas import (
    CalculateRewardResponse,
    PromoApplyRequest,
    PromoApplyResponse,
    PromoRedeemRequest,
    PromoRedeemResponse,
)
from .service import DailyPassBookingsService

router = APIRouter(prefix="/dailypass_details", tags=["DailyPassBookings V2"])


@router.get("/data", response_model=CalculateRewardResponse)
@log_exceptions
async def daily_pass_data(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
    gym_id: int = Query(...),
    number_of_days: int = Query(..., ge=0),
    head_count: Optional[int] = Query(None, ge=0),
):
  
    service = DailyPassBookingsService(db, redis)
    result = await service.calculate_reward(client_id, gym_id, number_of_days, head_count)

    return result


@router.post("/apply_coupon", response_model=PromoApplyResponse)
@log_exceptions
async def apply_coupon(
    request: Request,
    body: PromoApplyRequest,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):

    service = DailyPassBookingsService(db, redis)
    return await service.apply_coupon(client_id, body.coupon_code)


@router.post("/redeem_promo", response_model=PromoRedeemResponse)
@log_exceptions
async def redeem_promo_code(
    request: Request,
    body: PromoRedeemRequest,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):

    service = DailyPassBookingsService(db, redis)
    result = await service.redeem_promo(
        client_id=client_id,
        gym_id=body.gym_id,
        coupon_code=body.coupon_code,
        selected_date=body.selected_date,
    )

    return result

