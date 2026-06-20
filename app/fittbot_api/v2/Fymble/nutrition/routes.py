

from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.idor_protection import get_verified_client_id
from app.utils.redis_config import get_redis

from .schemas import NutritionPageResponse
from .service import NutritionPageService

router = APIRouter(
    prefix="/nutrition",
    tags=["Fymble Nutrition Page"],
)



@router.get("/page", response_model=NutritionPageResponse)
async def get_nutrition_page(
    client_id: int = Depends(get_verified_client_id),
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
) -> NutritionPageResponse:
    return await NutritionPageService(db, redis).get_page(client_id)


