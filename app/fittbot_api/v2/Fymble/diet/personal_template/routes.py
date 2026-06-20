"""Thin FastAPI endpoints for Diet Personal Templates.

No business logic here — delegates everything to the service layer.
"""

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions

from .schemas import (
    AddDietTemplateRequest,
    UpdateDietTemplateRequest,
    EditDietTemplateNameRequest,
    TemplateListResponse,
    SingleTemplateResponse,
    AddTemplateResponse,
    MessageResponse,
    CommonFoodResponse,
    SearchFoodResponse,
)
from .service import PersonalTemplateService

router = APIRouter(prefix="/diet_personal_template", tags=["DietPersonalTemplate V2"])


@router.get("/get", response_model=TemplateListResponse)
@log_exceptions
async def get_client_diet_templates(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
):
    service = PersonalTemplateService(db)
    return await service.list_templates(client_id)


@router.get("/get_single_diet_template", response_model=SingleTemplateResponse)
@log_exceptions
async def get_single_template(
    request: Request,
    id: int = Query(...),
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
):
    service = PersonalTemplateService(db)
    return await service.get_template(client_id, id)


@router.post("/add", response_model=AddTemplateResponse)
@log_exceptions
async def add_diet_template(
    req: AddDietTemplateRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
):
    service = PersonalTemplateService(db)
    return await service.add_template(client_id, req)


@router.put("/edit", response_model=MessageResponse)
@log_exceptions
async def edit_diet_template(
    req: UpdateDietTemplateRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
):
    service = PersonalTemplateService(db)
    return await service.edit_template_data(client_id, req)


@router.put("/update", response_model=MessageResponse)
@log_exceptions
async def update_diet_template_name(
    req: EditDietTemplateNameRequest,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
):
    service = PersonalTemplateService(db)
    return await service.edit_template_name(client_id, req)


@router.delete("/delete", response_model=MessageResponse)
@log_exceptions
async def delete_diet_template(
    request: Request,
    id: int = Query(...),
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
):
    service = PersonalTemplateService(db)
    return await service.delete_template(client_id, id)


# ── Common Food ──────────────────────────────────────────────────

@router.get("/common_food/consumed", response_model=CommonFoodResponse)
@log_exceptions
async def get_common_foods(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
):
    service = PersonalTemplateService(db)
    return await service.get_common_foods()


@router.get("/common_food/search", response_model=SearchFoodResponse)
@log_exceptions
async def search_food(
    request: Request,
    query: str = Query(..., min_length=2),
    db: AsyncSession = Depends(get_async_db),
    client_id: int = Depends(get_verified_client_id),
):
    service = PersonalTemplateService(db)
    return await service.search_foods(query)
