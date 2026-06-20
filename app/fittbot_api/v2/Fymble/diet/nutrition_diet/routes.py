from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions

from .schemas import AddStepRequest, GetNutritionDietResponse, MessageResponse
from .service import NutritionDietService

router = APIRouter(prefix="/nutrition_diet", tags=["Nutrition Diet V2"])


@router.get("/get", response_model=GetNutritionDietResponse)
@log_exceptions
async def get_nutrition_diet(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
):
    service = NutritionDietService(db)
    
    return await service.get_nutrition_diet(client_id)


@router.post("/add_step", response_model=MessageResponse)
@log_exceptions
async def add_step(
    req: AddStepRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
):
    service = NutritionDietService(db)
    return await service.add_step(client_id, req)
