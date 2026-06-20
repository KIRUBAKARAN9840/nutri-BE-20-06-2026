"""
FastAPI router for Google Play (RevenueCat) food-scanner credit purchases.

Webhook is handled by the shared Fymble_Payments webhook router —
NOT duplicated here.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from redis.asyncio import Redis as AsyncRedis

from app.utils.idor_protection import get_verified_client_id
from app.utils.redis_config import get_redis
from ..._deps.config import HighConcurrencyConfig, get_high_concurrency_config

from ...shared.async_command_store import AsyncCommandStore
from ..shared.schemas import (
    CommandStatusResponse,
    CreditCommandAccepted,
)
from .dispatcher import GooglePlayCreditsDispatcher
from .schemas import CreditPurchaseRequest, CreditVerifyRequest

router = APIRouter(prefix="/credits/googleplay", tags=["Food Scanner Credits - Google Play"])


# ── Async dependencies (native async Redis) ────────────────────────

async def _get_config() -> HighConcurrencyConfig:
    return get_high_concurrency_config()


async def _get_async_store(
    redis: AsyncRedis = Depends(get_redis),
    config: HighConcurrencyConfig = Depends(_get_config),
) -> AsyncCommandStore:
    return AsyncCommandStore(
        redis,
        config,
        redis_prefix=config.credits_redis_prefix,
        command_id_prefix="cr_cmd",
    )


async def _get_dispatcher(
    store: AsyncCommandStore = Depends(_get_async_store),
    config: HighConcurrencyConfig = Depends(_get_config),
) -> GooglePlayCreditsDispatcher:
    return GooglePlayCreditsDispatcher(store, config)


# ── Purchase ────────────────────────────────────────────────────────

@router.post(
    "/purchase",
    response_model=CreditCommandAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_credit_purchase(
    request: Request,
    body: CreditPurchaseRequest,
    client_id: int = Depends(get_verified_client_id),
    dispatcher: GooglePlayCreditsDispatcher = Depends(_get_dispatcher),
):
    record = await dispatcher.enqueue_purchase(body, client_id=str(client_id))
    status_url = request.url_for(
        "get_gp_credit_command_status", command_id=record.command_id
    )

    from app.services.activity_tracker import track_event

    await track_event(
        client_id,
        "checkout_initiated",
        product_type="food_scanner_credits",
        product_details={"plan_sku": body.product_sku},
        source="payment_credits_googleplay",
        command_id=record.command_id,
    )

    return CreditCommandAccepted(
        request_id=record.command_id,
        status=record.status,
        status_url=str(status_url),
    )



@router.post(
    "/verify",
    response_model=CreditCommandAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_credit_verify(
    request: Request,
    body: CreditVerifyRequest,
    client_id: int = Depends(get_verified_client_id),
    dispatcher: GooglePlayCreditsDispatcher = Depends(_get_dispatcher),
):
    record = await dispatcher.enqueue_verify(body, client_id=str(client_id))
    status_url = request.url_for(
        "get_gp_credit_command_status", command_id=record.command_id
    )

    from app.services.activity_tracker import track_event

    await track_event(
        client_id,
        "checkout_completed",
        product_type="food_scanner_credits",
        source="payment_credits_googleplay",
        command_id=record.command_id,
    )

    return CreditCommandAccepted(
        request_id=record.command_id,
        status=record.status,
        status_url=str(status_url),
    )



@router.get(
    "/commands/{command_id}",
    response_model=CommandStatusResponse,
    name="get_gp_credit_command_status",
)
async def get_gp_credit_command_status(
    command_id: str,
    client_id: int = Depends(get_verified_client_id),
    store: AsyncCommandStore = Depends(_get_async_store),
):
    record = await store.get(command_id, owner_id=str(client_id))
    if not record:
        raise HTTPException(status_code=404, detail="command_not_found")
    return record.to_response()


