"""
Razorpay webhook router for all Fymble v2 payment types.

Receives Razorpay payment.captured / payment.failed events and routes
to the correct processor based on the `flow` field in order notes:

    food_scanner_credits_razorpay  -> credits processor
    (future) subscription_*       -> subscriptions processor

For non-v2 flows, the event is ignored here (handled by v1 WebhookProcessor).
"""

import hashlib
import hmac
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Request, status
from redis.asyncio import Redis as AsyncRedis

from app.celery_app import celery_app
from app.utils.redis_config import get_redis
from .._deps.config import HighConcurrencyConfig, get_high_concurrency_config

from ..shared.async_command_store import AsyncCommandStore



logger = logging.getLogger("payments.fymble.razorpay.webhook")

router = APIRouter(tags=["Fymble Payments - Razorpay Webhook"])



CREDITS_FLOWS = ("food_scanner_credits_razorpay",)
SUBSCRIPTION_FLOWS = ("subscription_razorpay_v2",)
NUTRITION_PKG_FLOWS = ("nutrition_package_razorpay",)

# Legacy v1 flows — dispatched to the v1 umbrella task
# `payments.razorpay.process_webhook`, which internally fans out to
# DailyPass / Session / GymMembership / NutritionPurchase fulfillment.
DAILYPASS_FLOWS = (
    "dailypass_only",
    "unified_dailypass_local_sub",
    "dailypass_upgrade",
    "dailypass_edit_topup",
)
GYM_MEMBERSHIP_FLOW_SUBSTRINGS = ("gym_membership", "personal_training")
NUTRITION_PURCHASE_FLOW = "nutrition_purchase"
SESSION_TYPE_MARKER = "session_booking"  # notes.type, not notes.flow
V1_WEBHOOK_TASK = "payments.razorpay.process_webhook"
V1_WEBHOOK_CMD_PREFIX = "rzp_cmd"


async def _get_config() -> HighConcurrencyConfig:
    return get_high_concurrency_config()


async def _get_redis_client() -> AsyncRedis:
    return await get_redis()


def _verify_webhook_signature(
    raw_body: bytes,
    signature: str,
    webhook_secret: str,
) -> bool:
    """Verify Razorpay webhook signature (HMAC-SHA256)."""
    try:
        expected = hmac.new(
            webhook_secret.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False


@router.post(
    "/fymble_payments/razorpay/webhook",
    status_code=status.HTTP_202_ACCEPTED,
)
async def razorpay_webhook(
    request: Request,
    redis: AsyncRedis = Depends(_get_redis_client),
    config: HighConcurrencyConfig = Depends(_get_config),
):

    raw_body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    raw_str = raw_body.decode()

    # 1. Validate Razorpay webhook signature
    from .._deps.config import get_payment_settings
    settings = get_payment_settings()

    if not _verify_webhook_signature(raw_body, signature, settings.razorpay_webhook_secret):
        logger.warning("RP_WEBHOOK_INVALID_SIGNATURE")
        return {"status": "invalid_signature"}, 401

    # 2. Extract flow from payment notes
    flow = _extract_flow(raw_str)
    route = _resolve_route(flow, config)

    # DB fallback: if notes-based routing fails but the event carries an
    # order_id, forward to the v1 umbrella task. WebhookProcessor already
    # looks up Order.order_metadata to classify dailypass / gym_membership /
    # session / nutrition_purchase when notes are missing.
    if route is None:
        order_id = _extract_order_id(raw_str)
        if order_id:
            logger.info(
                "RP_WEBHOOK_DB_FALLBACK | flow=%s order_id=%s",
                flow or "empty",
                order_id,
            )
            route = _v1_route(config)
        else:
            logger.info("RP_WEBHOOK_SKIPPED | flow=%s (no route)", flow)
            return {"status": "skipped", "reason": "unrouted_flow"}

    # 3. Build payload in the shape the target worker expects.
    #    v2 workers (credits/subscription) read `raw_body` + `razorpay_signature`.
    #    v1 WebhookProcessor reads `raw_body` + `signature` + top-level JSON.
    if route.get("payload_shape") == "v1":
        try:
            parsed = json.loads(raw_str)
        except json.JSONDecodeError:
            parsed = {}
        command_payload = {
            **parsed,
            "raw_body": raw_str,
            "signature": signature,
            "webhook_id": parsed.get("id"),
        }
    else:
        command_payload = {
            "raw_body": raw_str,
            "razorpay_signature": signature,
        }

    # 4. Enqueue to the correct Celery processor
    store = AsyncCommandStore(
        redis,
        config,
        redis_prefix=route["redis_prefix"],
        command_id_prefix=route["command_id_prefix"],
    )

    record = await store.create(
        command_type=route["command_type"],
        payload=command_payload,
    )

    celery_app.send_task(
        route["task_name"],
        args=[record.command_id],
        queue="payments",
    )

    logger.info(
        "RP_WEBHOOK_ROUTED | flow=%s route=%s command=%s",
        flow or "unknown",
        route["name"],
        record.command_id,
    )

    return {
        "request_id": record.command_id,
        "status": record.status,
        "routed_to": route["name"],
    }


# ── Helpers ─────────────────────────────────────────────────────────

def _extract_flow(raw_body: str) -> str:
    """Best-effort flow extraction from Razorpay webhook JSON notes.

    Falls back to `notes.type == "session_booking"` because the session
    booking flow marks orders with `type`, not `flow`.
    """
    try:
        payload = json.loads(raw_body)
        payment_entity = (
            payload.get("payload", {}).get("payment", {}).get("entity", {})
        )
        notes = payment_entity.get("notes", {})
        flow = notes.get("flow", "") or ""
        if not flow and notes.get("type") == SESSION_TYPE_MARKER:
            return SESSION_TYPE_MARKER
        return flow
    except (json.JSONDecodeError, AttributeError):
        return ""


def _is_gym_membership_flow(flow: str) -> bool:
    return any(s in flow for s in GYM_MEMBERSHIP_FLOW_SUBSTRINGS)


def _resolve_route(flow: str, config: HighConcurrencyConfig) -> Optional[dict]:

    if flow in CREDITS_FLOWS:
        return {
            "name": "rp_credits",
            "task_name": config.rp_credits_webhook_queue_name,
            "redis_prefix": config.rp_credits_redis_prefix,
            "command_id_prefix": "rpcr_cmd",
            "command_type": "rp_credits_webhook",
        }

    if flow in SUBSCRIPTION_FLOWS:
        return {
            "name": "rp_subscription",
            "task_name": config.rp_subscription_webhook_queue_name,
            "redis_prefix": config.rp_subscription_redis_prefix,
            "command_id_prefix": "rpsub_cmd",
            "command_type": "rp_subscription_webhook",
        }

    if flow in NUTRITION_PKG_FLOWS:
        return {
            "name": "rp_nutrition_pkg",
            "task_name": config.rp_nutrition_pkg_webhook_queue_name,
            "redis_prefix": config.rp_nutrition_pkg_redis_prefix,
            "command_id_prefix": "rpnutr_pkg_cmd",
            "command_type": "rp_nutrition_pkg_webhook",
        }

    # v1 legacy flows — single umbrella task handles dailypass, session,
    # gym_membership, and nutrition_purchase fulfillment internally.
    if (
        flow in DAILYPASS_FLOWS
        or flow == SESSION_TYPE_MARKER
        or flow == NUTRITION_PURCHASE_FLOW
        or _is_gym_membership_flow(flow)
    ):
        return _v1_route(config)

    return None


def _v1_route(config: HighConcurrencyConfig) -> dict:
    return {
        "name": "v1_razorpay_webhook",
        "task_name": V1_WEBHOOK_TASK,
        "redis_prefix": config.redis_prefix,
        "command_id_prefix": V1_WEBHOOK_CMD_PREFIX,
        "command_type": "webhook",
        "payload_shape": "v1",
    }


def _extract_order_id(raw_body: str) -> str:
    try:
        payload = json.loads(raw_body)
        pay_entity = (
            payload.get("payload", {}).get("payment", {}).get("entity", {})
        )
        return pay_entity.get("order_id") or ""
    except (json.JSONDecodeError, AttributeError):
        return ""
