from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.async_database import get_async_db
from app.utils.redis_config import get_redis
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions

from .schemas import WeightProgressResponse, AddWeightRequest, AddWeightResponse
from .service import WeightProgressService

router = APIRouter(prefix="/weight_progress", tags=["Weight Progress V2"])


@router.get("/data", response_model=WeightProgressResponse)
@log_exceptions
async def get_weight_progress_data(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
):
    service = WeightProgressService(db)
    return await service.get_data(client_id)



@router.post("/add_weight", response_model=AddWeightResponse)
@log_exceptions
async def add_weight(
    req: AddWeightRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = WeightProgressService(db, redis)
    return await service.add_weight(client_id, req)
