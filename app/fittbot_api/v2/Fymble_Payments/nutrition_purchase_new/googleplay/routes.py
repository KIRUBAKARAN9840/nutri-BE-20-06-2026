
from fastapi import APIRouter, Depends, HTTPException, Request, status
from redis.asyncio import Redis as AsyncRedis

from app.utils.idor_protection import get_verified_client_id
from app.utils.redis_config import get_redis
from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits._deps.config import (
    HighConcurrencyConfig,
    get_high_concurrency_config,
)
from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits.shared.async_command_store import (
    AsyncCommandStore,
)
from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits.credits.shared.schemas import (
    CommandStatusResponse,
    CreditCommandAccepted,
)
from ..plans import UnknownPlanError, get_plan
from .dispatcher import GooglePlayNutritionPackageDispatcher
from .schemas import NutritionPackagePurchaseRequest, NutritionPackageVerifyRequest

router = APIRouter(
    prefix="/nutrition_purchase_new/googleplay",
    tags=["Nutrition Package Purchase - Google Play"],
)


# ── Async dependencies ────────────────────────────────────────────────

async def _get_config() -> HighConcurrencyConfig:
    return get_high_concurrency_config()


async def _get_async_store(
    redis: AsyncRedis = Depends(get_redis),
    config: HighConcurrencyConfig = Depends(_get_config),
) -> AsyncCommandStore:
    return AsyncCommandStore(
        redis,
        config,
        redis_prefix=config.gp_nutrition_redis_prefix,
        command_id_prefix="nutr_pkg_cmd",
    )


async def _get_dispatcher(
    store: AsyncCommandStore = Depends(_get_async_store),
    config: HighConcurrencyConfig = Depends(_get_config),
) -> GooglePlayNutritionPackageDispatcher:
    return GooglePlayNutritionPackageDispatcher(store, config)


# ── Purchase (no slot info needed) ───────────────────────────────────

@router.post(
    "/purchase",
    response_model=CreditCommandAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_nutrition_package_purchase(
    request: Request,
    body: NutritionPackagePurchaseRequest,
    client_id: int = Depends(get_verified_client_id),
    dispatcher: GooglePlayNutritionPackageDispatcher = Depends(_get_dispatcher),
):
    try:
        plan = get_plan(body.product_sku)
    except UnknownPlanError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    record = await dispatcher.enqueue_purchase(body, client_id=str(client_id))
    status_url = request.url_for(
        "get_gp_nutrition_package_command_status", command_id=record.command_id
    )

    from app.services.activity_tracker import track_event

    await track_event(
        client_id,
        "checkout_initiated",
        product_type="nutrition_package",
        product_details={
            "plan_sku": body.product_sku,
            "total_sessions": plan.total_sessions,
        },
        source="payment_nutrition_package_googleplay",
        command_id=record.command_id,
    )

    return CreditCommandAccepted(
        request_id=record.command_id,
        status=record.status,
        status_url=str(status_url),
    )


# ── Verify ────────────────────────────────────────────────────────────

@router.post(
    "/verify",
    response_model=CreditCommandAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_nutrition_package_verify(
    request: Request,
    body: NutritionPackageVerifyRequest,
    client_id: int = Depends(get_verified_client_id),
    dispatcher: GooglePlayNutritionPackageDispatcher = Depends(_get_dispatcher),
):
    record = await dispatcher.enqueue_verify(body, client_id=str(client_id))
    status_url = request.url_for(
        "get_gp_nutrition_package_command_status", command_id=record.command_id
    )

    from app.services.activity_tracker import track_event

    await track_event(
        client_id,
        "checkout_completed",
        product_type="nutrition_package",
        source="payment_nutrition_package_googleplay",
        command_id=record.command_id,
    )

    return CreditCommandAccepted(
        request_id=record.command_id,
        status=record.status,
        status_url=str(status_url),
    )


# ── Command status polling ────────────────────────────────────────────

@router.get(
    "/commands/{command_id}",
    response_model=CommandStatusResponse,
    name="get_gp_nutrition_package_command_status",
)
async def get_gp_nutrition_package_command_status(
    command_id: str,
    client_id: int = Depends(get_verified_client_id),
    store: AsyncCommandStore = Depends(_get_async_store),
):
    record = await store.get(command_id, owner_id=str(client_id))
    if not record:
        raise HTTPException(status_code=404, detail="command_not_found")
    return record.to_response()
