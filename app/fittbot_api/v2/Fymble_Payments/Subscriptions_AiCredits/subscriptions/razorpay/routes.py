"""
FastAPI router for Razorpay subscription purchases.

Webhook is handled by the shared razorpay_webhook router —
NOT duplicated here.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from redis.asyncio import Redis as AsyncRedis

from app.utils.idor_protection import get_verified_client_id
from app.utils.redis_config import get_redis
from ..._deps.config import HighConcurrencyConfig, get_high_concurrency_config

from ...shared.async_command_store import AsyncCommandStore
from ..shared.schemas import (
    CommandStatusResponse,
    SubscriptionCommandAccepted,
)
from .dispatcher import RazorpaySubscriptionDispatcher
from .schemas import RpSubscriptionCheckoutRequest, RpSubscriptionVerifyRequest

logger = logging.getLogger("payments.v2.subscriptions.razorpay.routes")

router = APIRouter(
    prefix="/subscriptions/razorpay",
    tags=["Subscriptions - Razorpay"],
)


# ── Async dependencies ────────────────────────────────────────────

async def _get_config() -> HighConcurrencyConfig:
    return get_high_concurrency_config()


async def _get_async_store(
    redis: AsyncRedis = Depends(get_redis),
    config: HighConcurrencyConfig = Depends(_get_config),
) -> AsyncCommandStore:
    return AsyncCommandStore(
        redis,
        config,
        redis_prefix=config.rp_subscription_redis_prefix,
        command_id_prefix="rpsub_cmd",
    )


async def _get_dispatcher(
    store: AsyncCommandStore = Depends(_get_async_store),
    config: HighConcurrencyConfig = Depends(_get_config),
) -> RazorpaySubscriptionDispatcher:
    return RazorpaySubscriptionDispatcher(store, config)


# ── Checkout ──────────────────────────────────────────────────────

@router.post(
    "/checkout",
    response_model=SubscriptionCommandAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_rp_subscription_checkout(
    request: Request,
    body: RpSubscriptionCheckoutRequest,
    client_id: int = Depends(get_verified_client_id),
    dispatcher: RazorpaySubscriptionDispatcher = Depends(_get_dispatcher),
):
    record = await dispatcher.enqueue_checkout(body, user_id=str(client_id))
    status_url = request.url_for(
        "get_rp_subscription_command_status", command_id=record.command_id
    )

    from app.services.activity_tracker import track_event

    await track_event(
        client_id,
        "checkout_initiated",
        product_type="subscription",
        product_details={"plan_sku": body.plan_sku},
        source="payment_subscription_razorpay_v2",
        command_id=record.command_id,
    )

    return SubscriptionCommandAccepted(
        request_id=record.command_id,
        status=record.status,
        status_url=str(status_url),
    )


# ── Verify ────────────────────────────────────────────────────────

@router.post(
    "/verify",
    response_model=SubscriptionCommandAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def enqueue_rp_subscription_verify(
    request: Request,
    body: RpSubscriptionVerifyRequest,
    client_id: int = Depends(get_verified_client_id),
    dispatcher: RazorpaySubscriptionDispatcher = Depends(_get_dispatcher),
):
    record = await dispatcher.enqueue_verify(body, user_id=str(client_id))
    status_url = request.url_for(
        "get_rp_subscription_command_status", command_id=record.command_id
    )

    from app.services.activity_tracker import track_event

    await track_event(
        client_id,
        "checkout_completed",
        product_type="subscription",
        source="payment_subscription_razorpay_v2",
        command_id=record.command_id,
    )

    return SubscriptionCommandAccepted(
        request_id=record.command_id,
        status=record.status,
        status_url=str(status_url),
    )


# ── Command status polling ────────────────────────────────────────

@router.get(
    "/commands/{command_id}",
    response_model=CommandStatusResponse,
    name="get_rp_subscription_command_status",
)
async def get_rp_subscription_command_status(
    command_id: str,
    client_id: int = Depends(get_verified_client_id),
    store: AsyncCommandStore = Depends(_get_async_store),
):
    record = await store.get(command_id, owner_id=str(client_id))
    if not record:
        raise HTTPException(status_code=404, detail="command_not_found")
    return record.to_response()
