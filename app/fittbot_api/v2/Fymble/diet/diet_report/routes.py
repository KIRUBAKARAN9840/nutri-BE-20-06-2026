"""Thin FastAPI endpoints for Diet Report.

No business logic here — delegates everything to the service layer.
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.redis_config import get_redis
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions

from .schemas import DietReportResponse
from .service import DietReportService

router = APIRouter(prefix="/diet_report", tags=["Diet Report V2"])


@router.get("/get", response_model=DietReportResponse)
@log_exceptions
async def get_diet_report(
    request: Request,
    report_date: Optional[date] = Query(None, alias="date"),
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = DietReportService(db, redis)

    return await service.get_report(client_id, report_date)
