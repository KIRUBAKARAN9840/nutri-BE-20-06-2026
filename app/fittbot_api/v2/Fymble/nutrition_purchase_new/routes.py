"""
FastAPI routes for the new nutrition purchase flow (4-session package).

Endpoints:
  GET  /nutrition_purchase_new/data    → package preview (price, session schedule)
  GET  /nutrition_purchase_new/status  → client's package status + next session info
  GET  /nutrition_purchase_new/dates   → available booking dates
  GET  /nutrition_purchase_new/slots   → user-aware slot list for a date
  POST /nutrition_purchase_new/book    → book a session (post-purchase)
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.idor_protection import get_verified_client_id
from app.utils.redis_config import get_redis

from .schemas import (
    BookSlotRequest,
    BookSlotResponse,
    DatesResponse,
    NutritionPackagePreviewResponse,
    NutritionPackageStatusResponse,
    SlotsResponse,
)
from .service import NutritionPurchaseNewService

router = APIRouter(
    prefix="/nutrition_purchase_new",
    tags=["Nutrition Purchase New - 4 Session Package"],
)


def _get_service(
    db: AsyncSession = Depends(get_async_db),
    redis: AsyncRedis = Depends(get_redis),
) -> NutritionPurchaseNewService:
    return NutritionPurchaseNewService(db, redis)


# ── GET /data — package preview ──────────────────────────────────────

@router.get("/data", response_model=NutritionPackagePreviewResponse)
async def get_nutrition_data(
    service: NutritionPurchaseNewService = Depends(_get_service),
):
    return await service.get_preview()


# ── GET /status — client package status ──────────────────────────────

@router.get("/status", response_model=NutritionPackageStatusResponse)
async def get_nutrition_status(
    client_id: int = Depends(get_verified_client_id),
    service: NutritionPurchaseNewService = Depends(_get_service),
):
    return await service.get_package_status(client_id)


# ── GET /dates — available dates ─────────────────────────────────────

@router.get("/dates", response_model=DatesResponse)
async def get_available_dates(
    service: NutritionPurchaseNewService = Depends(_get_service),
):
    return await service.get_available_dates()


# ── GET /slots — user-aware slot list ────────────────────────────────

@router.get("/slots", response_model=SlotsResponse)
async def get_slots(
    date_str: str = Query(..., alias="date", description="ISO date YYYY-MM-DD"),
    client_id: int = Depends(get_verified_client_id),
    service: NutritionPurchaseNewService = Depends(_get_service),
):
    try:
        selected_date = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid_date_format")
    return await service.get_slots_for_date(client_id, selected_date)


# ── POST /book — book a session post-purchase ────────────────────────

@router.post("/book", response_model=BookSlotResponse)
async def book_slot(
    body: BookSlotRequest,
    client_id: int = Depends(get_verified_client_id),
    service: NutritionPurchaseNewService = Depends(_get_service),
):
    try:
        result = await service.book_slot(client_id, body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result
