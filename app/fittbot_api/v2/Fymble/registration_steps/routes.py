from fastapi import APIRouter, Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.async_database import get_async_db
from app.utils.idor_protection import get_verified_client_id
from app.utils.logging_utils import log_exceptions
from app.utils.redis_config import get_redis

from .schemas import (
    BodyShapeStepRequest,
    DOBStepRequest,
    GoalStepRequest,
    HeightStepRequest,
    LifestyleStepRequest,
    StepResponse,
    StepsStatusResponse,
    WeightStepRequest,
)
from .service import RegistrationStepsService

router = APIRouter(prefix="/client/new_registration", tags=["Registration Steps V2"])


def _get_service(
    db: AsyncSession = Depends(get_async_db),
    redis: Redis = Depends(get_redis),
) -> RegistrationStepsService:
    return RegistrationStepsService(db, redis)


# ── Registration Steps (all authenticated, client_id from JWT) ──────────────

@router.post("/step/dob", response_model=StepResponse)
@log_exceptions
async def update_dob_step(
    body: DOBStepRequest,
    svc: RegistrationStepsService = Depends(_get_service),
    client_id: int = Depends(get_verified_client_id),
):
    return await svc.update_dob(client_id, body)


@router.post("/step/goal", response_model=StepResponse)
@log_exceptions
async def update_goal_step(
    body: GoalStepRequest,
    svc: RegistrationStepsService = Depends(_get_service),
    client_id: int = Depends(get_verified_client_id),
):
    return await svc.update_goal(client_id, body)


@router.post("/step/height", response_model=StepResponse)
@log_exceptions
async def update_height_step(
    body: HeightStepRequest,
    svc: RegistrationStepsService = Depends(_get_service),
    client_id: int = Depends(get_verified_client_id),
):
    return await svc.update_height(client_id, body)


@router.post("/step/weight", response_model=StepResponse)
@log_exceptions
async def update_weight_step(
    body: WeightStepRequest,
    svc: RegistrationStepsService = Depends(_get_service),
    client_id: int = Depends(get_verified_client_id),
):
    return await svc.update_weight(client_id, body)


@router.post("/step/body-shape", response_model=StepResponse)
@log_exceptions
async def update_body_shape_step(
    body: BodyShapeStepRequest,
    svc: RegistrationStepsService = Depends(_get_service),
    client_id: int = Depends(get_verified_client_id),
):
    return await svc.update_body_shape(client_id, body)


@router.post("/step/lifestyle", response_model=StepResponse)
@log_exceptions
async def update_lifestyle_step(
    body: LifestyleStepRequest,
    svc: RegistrationStepsService = Depends(_get_service),
    client_id: int = Depends(get_verified_client_id),
):
    return await svc.update_lifestyle(client_id, body)


# ── Steps Status (authenticated) ────────────────────────────────────────────

@router.get("/steps-status", response_model=StepsStatusResponse)
@log_exceptions
async def get_registration_steps_status(
    svc: RegistrationStepsService = Depends(_get_service),
    client_id: int = Depends(get_verified_client_id),
):
    return await svc.get_steps_status(client_id)
