
from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.redis_config import get_redis
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions

from .schemas import SidebarDataResponse
from .service import SidebarService

router = APIRouter(prefix="/sidebar", tags=["Sidebar V2"])


@router.get("/data", response_model=SidebarDataResponse)
@log_exceptions
async def get_sidebar_data(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = SidebarService(db, redis)
    return await service.get_sidebar_data(client_id)
