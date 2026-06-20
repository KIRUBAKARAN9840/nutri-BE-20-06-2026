from fastapi import APIRouter, Depends, Path, Request, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions
from app.utils.redis_config import get_redis

from .schemas import (
    ChatDietGenerateResponse,
    ChatDietRequest,
    CurrentPlanResponse,
    FollowupEligibilityResponse,
    FollowupGenerateRequest,
    JobPlanResponse,
    JobStatusResponse,
    SwapMealRequest,
    SwapMealResponse,
)
from .service import ChatDietService

router = APIRouter(prefix="/chat-diet", tags=["Chat Diet V2"])


@router.post("/generate", response_model=ChatDietGenerateResponse)
@log_exceptions
async def chat_diet_generate(
    request: Request,
    body: ChatDietRequest,
    response: Response,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = ChatDietService(db, redis)
    result = await service.process_chat(body, client_id)
    response.status_code = result.status
    return result


@router.get("/status/{job_id}", response_model=JobStatusResponse)
@log_exceptions
async def chat_diet_status(
    request: Request,
    job_id: str = Path(..., min_length=8, max_length=80),
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = ChatDietService(db, redis)
    return await service.get_status(job_id, client_id)


@router.get("/plan/{job_id}", response_model=JobPlanResponse)
@log_exceptions
async def chat_diet_plan(
    request: Request,
    job_id: str = Path(..., min_length=8, max_length=80),
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = ChatDietService(db, redis)
    return await service.get_plan(job_id, client_id)


@router.get("/current", response_model=CurrentPlanResponse)
@log_exceptions
async def chat_diet_current(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = ChatDietService(db, redis)
    return await service.get_current_state(client_id)


@router.get("/followup/eligibility", response_model=FollowupEligibilityResponse)
@log_exceptions
async def chat_diet_followup_eligibility(
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = ChatDietService(db, redis)
    return await service.get_followup_eligibility(client_id)


@router.post("/followup/generate", response_model=ChatDietGenerateResponse)
@log_exceptions
async def chat_diet_followup_generate(
    request: Request,
    body: FollowupGenerateRequest,
    response: Response,
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = ChatDietService(db, redis)
    result = await service.enqueue_followup(body, client_id)
    response.status_code = result.status
    return result


@router.post("/plans/{plan_id}/swap", response_model=SwapMealResponse)
@log_exceptions
async def chat_diet_swap_meal(
    request: Request,
    body: SwapMealRequest,
    plan_id: int = Path(..., gt=0),
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
    client_id: int = Depends(get_verified_client_id),
):
    service = ChatDietService(db, redis)
    return await service.swap_meal(plan_id, body, client_id)
