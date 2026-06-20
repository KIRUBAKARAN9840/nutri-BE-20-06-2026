"""Routes for Food Scanner API v2.

HTTP endpoints for food scanning, analysis, and database operations.
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, File, UploadFile, Form, Request, Response
from redis.asyncio import Redis
from sqlalchemy.orm import Session

from app.models.database import get_db
from app.utils.redis_config import get_redis
from app.utils.logging_utils import log_exceptions

from .schemas import (
    AnalyzeResponse,
    AnalyzeTextRequest,
    AnalyzeAsyncRequest,
    AnalyzeAsyncResponse,
    JobStatusResponse,
    ModifyItemsRequest,
    ModifyItemsResponse,
    SaveFoodRequest,
    SaveFoodResponse,
    HealthCheckResponse,
    CreateAIDietRequest,
    CreateAIDietResponse,
)
from .service import FoodScannerService

router = APIRouter(prefix="/food_scanner", tags=["Food Scanner V2"])

# Separate router for diet endpoints (to match frontend API path)
diet_router = APIRouter(prefix="/diet", tags=["Diet V2"])



@router.post("/analyze", response_model=AnalyzeResponse)
@log_exceptions
async def analyze_images(
    files: List[UploadFile] = File(...),
    food_scan: Optional[bool] = Form(None),
    client_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis),
):

    service = FoodScannerService(db, redis)
    return await service.analyze_images(files, client_id, food_scan)



@router.post("/modify_items", response_model=ModifyItemsResponse)
@log_exceptions
async def modify_items_endpoint(
    request: ModifyItemsRequest,
    response: Response,
    db: Session = Depends(get_db),
    redis: Redis = Depends(get_redis),
):
    """Add, edit, or delete a scanned food item and recalculate totals."""
    service = FoodScannerService(db, redis)
    edited_food = request.edited_food.model_dump() if request.edited_food else None
    result = await service.modify_items(
        action=request.action,
        item_id=request.item_id,
        items=[item.model_dump() for item in request.items],
        edited_food=edited_food,
        model=request.model,
    )
    response.status_code = result.status
    return result

