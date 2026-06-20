from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.logging_utils import FittbotHTTPException, log_exceptions

from .service import AppConfigService

router = APIRouter(prefix="/app-config", tags=["App Config V2"])


def _get_service(db: AsyncSession = Depends(get_async_db)) -> AppConfigService:
    return AppConfigService(db)


@router.get("/check")
@log_exceptions
async def check_app_config(
    app: str = Query(..., description="App identifier (fittbot / business)"),
    current_version: str = Query(..., description="Installed app version e.g. 2.1.0"),
    platform: Optional[str] = Query(None, description="Platform (android / ios)"),
    svc: AppConfigService = Depends(_get_service),
):
    """Single pre-login check: maintenance → redirect → force_update → ok."""
    try:
        return await svc.check(app, current_version, platform)
    except FittbotHTTPException:
        raise
    except Exception as e:
        raise FittbotHTTPException(
            status_code=500,
            detail="Failed to check app config",
            error_code="APP_CONFIG_CHECK_ERROR",
            log_data={"app": app, "version": current_version, "error": str(e)},
        )
