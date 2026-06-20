from fastapi import APIRouter, Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions
from app.utils.redis_config import get_redis

from .api import ReportsAPI, build_reports_api
from ._http_schemas import SubmitReportRequest, SubmitReportResponse


router = APIRouter(prefix="/gym_mate/reports", tags=["GymMate Reports V2"])


def _api(
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
) -> ReportsAPI:
    return build_reports_api(db, redis)


@router.post("", response_model=SubmitReportResponse)
@log_exceptions
async def submit_report(
    req: SubmitReportRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
    api: ReportsAPI = Depends(_api),
):
    data = await api.submit(
        reporter_id=client_id,
        entity_type=req.entity_type,
        entity_id=req.entity_id,
        reason=req.reason,
        details=req.details,
    )
    await db.commit()
    return SubmitReportResponse(data=data)
