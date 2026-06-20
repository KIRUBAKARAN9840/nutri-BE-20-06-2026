
from fastapi import APIRouter, Depends, Query, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.redis_config import get_redis
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions

from .repository import HomeRepository
from .schemas import (
    DismissFreeCreditsResponse,
    HomeDataParams,
    HomeDataResponse,
    IphoneNutritionPayload,
    IphoneNutritionResponse,
    NutritionJoinResponse,
    SaveGymRequestPayload,
    SaveGymRequestResponse,
)
from .service import HomeService

router = APIRouter(prefix="/home", tags=["Home V2"])


@router.get("/data", response_model=HomeDataResponse)
@log_exceptions
async def get_home_data(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
    client_lat: float = Query(..., description="Client latitude"),
    client_lng: float = Query(..., description="Client longitude"),
):
    
    params = HomeDataParams(
        client_lat=client_lat,
        client_lng=client_lng,
        client_id=client_id,
    )
    service = HomeService(db, redis)
    result = await service.get_home_data(params)
    return result


@router.post("/save_request", response_model=SaveGymRequestResponse)
@log_exceptions
async def save_gym_request(
    request: Request,
    payload: SaveGymRequestPayload,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    repo = HomeRepository(db, redis)
    is_new = await repo.save_gym_request(
        client_id=client_id,
        lat=payload.lat,
        lng=payload.lng,
        area=payload.area,
        city=payload.city,
        state=payload.state,
        pincode=payload.pincode,
    )
    if not is_new:
        return SaveGymRequestResponse(
            message="Already requested",
            already_requested=True,
        )
    return SaveGymRequestResponse()


@router.get("/nutrition/join", response_model=NutritionJoinResponse)
@log_exceptions
async def nutrition_join(
    request: Request,
    booking_id: int = Query(..., description="Nutrition booking ID"),
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = HomeService(db, redis)
    return await service.check_nutrition_join(booking_id, client_id)


@router.post("/dismiss_free_credits", response_model=DismissFreeCreditsResponse)
@log_exceptions
async def dismiss_free_credits(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    repo = HomeRepository(db, redis)
    await repo.dismiss_free_credits_card(client_id)
    return DismissFreeCreditsResponse()


@router.post("/iphone_nutrition", response_model=IphoneNutritionResponse)
@log_exceptions
async def save_iphone_nutrition(
    request: Request,
    payload: IphoneNutritionPayload,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    repo = HomeRepository(db, redis)
    inserted = await repo.upsert_iphone_nutrition(client_id, payload.type)
    
    if not inserted:
        return IphoneNutritionResponse(
            message="Already exists",
            already_exists=True,
        )
    return IphoneNutritionResponse()


