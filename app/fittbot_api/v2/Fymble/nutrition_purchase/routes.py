"""Thin FastAPI endpoints for Nutrition Purchase.

No business logic here — delegates everything to the service layer.
"""

from datetime import date

from fastapi import APIRouter, Depends, Query, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.redis_config import get_redis
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions

from .schemas import NutritionPurchasePreviewResponse, NutritionStatusResponse, DatesResponse, SlotsResponse
from .service import NutritionPurchaseService

router = APIRouter(prefix="/nutrition_purchase", tags=["NutritionPurchase V2"])


@router.get("/status", response_model=NutritionStatusResponse)
@log_exceptions
async def nutrition_status(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = NutritionPurchaseService(db, redis)
    result = await service.get_nutrition_status(client_id)
    return NutritionStatusResponse(**result)


@router.get("/data", response_model=NutritionPurchasePreviewResponse)
@log_exceptions
async def nutrition_purchase_data(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = NutritionPurchaseService(db, redis)
    return await service.get_preview(client_id=client_id)


@router.get("/dates", response_model=DatesResponse)
@log_exceptions
async def nutrition_purchase_dates(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
):
    service = NutritionPurchaseService(db, redis)
    return await service.get_available_dates()


@router.get("/slots", response_model=SlotsResponse)
@log_exceptions
async def nutrition_purchase_slots(
    request: Request,
    date: date = Query(...),
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
):
    service = NutritionPurchaseService(db, redis)
    return await service.get_slots_for_date(selected_date=date)
