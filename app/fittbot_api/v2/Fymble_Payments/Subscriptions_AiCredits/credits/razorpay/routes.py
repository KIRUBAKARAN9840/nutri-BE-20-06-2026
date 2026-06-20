"""
FastAPI router for Razorpay food-scanner credit purchases.
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
from .dispatcher import RazorpayCreditsDispatcher
from .schemas import RpCreditCheckoutRequest, RpCreditVerifyRequest

router = APIRouter(prefix="/credits/razorpay", tags=["Food Scanner Credits - Razorpay"])


# ── Async dependencies ─────────────────────────────────────────────

async def _get_config() -> HighConcurrencyConfig:
    return get_high_concurrency_config()


async def _get_async_store(
    redis: AsyncRedis = Depends(get_redis),
    config: HighConcurrencyConfig = Depends(_get_config),
) -> AsyncCommandStore:
    return AsyncCommandStore(
        redis,
        config,
        redis_prefix=config.rp_credits_redis_prefix,
        command_id_prefix="rpcr_cmd",
    )


async def _get_dispatcher(
    store: AsyncCommandStore = Depends(_get_async_store),
    config: HighConcurrencyConfig = Depends(_get_config),
) -> RazorpayCreditsDispatcher:
    return RazorpayCreditsDispatcher(store, config)


# ── Checkout ────────────────────────────────────────────────────────

@router.post(
    "/checkout",
    response_model=CreditCommandAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_rp_credit_checkout(
    request: Request,
    body: RpCreditCheckoutRequest,
    client_id: int = Depends(get_verified_client_id),
    dispatcher: RazorpayCreditsDispatcher = Depends(_get_dispatcher),
):
    record = await dispatcher.enqueue_checkout(body, client_id=str(client_id))
    status_url = request.url_for(
        "get_rp_credit_command_status", command_id=record.command_id
    )

    from app.services.activity_tracker import track_event

    await track_event(
        client_id,
        "checkout_initiated",
        product_type="food_scanner_credits",
        product_details={"plan_sku": body.product_sku},
        source="payment_credits_razorpay",
        command_id=record.command_id,
    )

    return CreditCommandAccepted(
        request_id=record.command_id,
        status=record.status,
        status_url=str(status_url),
    )


# ── Verify ──────────────────────────────────────────────────────────

@router.post(
    "/verify",
    response_model=CreditCommandAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_rp_credit_verify(
    request: Request,
    body: RpCreditVerifyRequest,
    client_id: int = Depends(get_verified_client_id),
    dispatcher: RazorpayCreditsDispatcher = Depends(_get_dispatcher),
):
    record = await dispatcher.enqueue_verify(body, client_id=str(client_id))
    status_url = request.url_for(
        "get_rp_credit_command_status", command_id=record.command_id
    )

    from app.services.activity_tracker import track_event

    await track_event(
        client_id,
        "checkout_completed",
        product_type="food_scanner_credits",
        source="payment_credits_razorpay",
        command_id=record.command_id,
    )

    return CreditCommandAccepted(
        request_id=record.command_id,
        status=record.status,
        status_url=str(status_url),
    )


# ── Command status polling ──────────────────────────────────────────

@router.get(
    "/commands/{command_id}",
    response_model=CommandStatusResponse,
    name="get_rp_credit_command_status",
)
async def get_rp_credit_command_status(
    command_id: str,
    client_id: int = Depends(get_verified_client_id),
    store: AsyncCommandStore = Depends(_get_async_store),
):
    record = await store.get(command_id, owner_id=str(client_id))
    if not record:
        raise HTTPException(status_code=404, detail="command_not_found")
    return record.to_response()
