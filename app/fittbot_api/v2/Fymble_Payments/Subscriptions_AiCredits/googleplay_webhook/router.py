
import json
import logging

from fastapi import APIRouter, Depends, Request, status
from redis.asyncio import Redis as AsyncRedis

from app.celery_app import celery_app
from app.fittbot_api.v2.Fymble_Payments.nutrition_purchase_new.plans import (
    get_plan_or_none,
)
from app.utils.redis_config import get_redis
from .._deps.config import HighConcurrencyConfig, get_high_concurrency_config

from ..shared.async_command_store import AsyncCommandStore

logger = logging.getLogger("payments.fymble.googleplay.webhook")

router = APIRouter(tags=["Fymble Payments - Google Play Webhook"])

# Credit SKUs still match by prefix (no central catalog for credits).
# Nutrition / AI-diet-coach SKUs are matched via the plans.py catalog so any
# new SKU added there auto-routes here without editing this file.
CREDIT_PRODUCT_PREFIXES = ("credit_",)


async def _get_config() -> HighConcurrencyConfig:
    return get_high_concurrency_config()


async def _get_redis_client() -> AsyncRedis:
    return await get_redis()


@router.post(
    "/fymble_payments/googleplay/webhook",
    status_code=status.HTTP_202_ACCEPTED,
)
async def googleplay_webhook(
    request: Request,
    redis: AsyncRedis = Depends(_get_redis_client),
    config: HighConcurrencyConfig = Depends(_get_config),
):
    """
    Single RevenueCat webhook endpoint for Google Play purchases.
    Peeks at product_id to route to the correct Celery processor.
    """
    raw_body = await request.body()
    signature = request.headers.get("Authorization", "").replace("Bearer ", "")
    raw_str = raw_body.decode()

    product_id = _extract_product_id(raw_str)
    route = _resolve_route(product_id, config)

    store = AsyncCommandStore(
        redis,
        config,
        redis_prefix=route["redis_prefix"],
        command_id_prefix=route["command_id_prefix"],
    )

    record = await store.create(
        command_type=route["command_type"],
        payload={"signature": signature, "raw_body": raw_str},
    )

    celery_app.send_task(
        route["task_name"],
        args=[record.command_id],
        queue="payments",
    )

    logger.info(
        "GP_WEBHOOK_ROUTED | product=%s route=%s command=%s",
        product_id or "unknown",
        route["name"],
        record.command_id,
    )

    return {
        "request_id": record.command_id,
        "status": record.status,
        "routed_to": route["name"],
    }


# ── Helpers ─────────────────────────────────────────────────────────

def _extract_product_id(raw_body: str) -> str:
    try:
        payload = json.loads(raw_body)
        event = payload.get("event", {})
        return event.get("product_id", "")
    except (json.JSONDecodeError, AttributeError):
        return ""


def _resolve_route(product_id: str, config: HighConcurrencyConfig) -> dict:
    """
    Single GP webhook dispatch — three buckets:

      1. credit_*  → credits processor
      2. any SKU in plans.py (nutri_basic, nutri_1m, nutri_3m, ai_diet_coach,
         and any future plan) → nutrition processor. Inside that processor,
         _fulfill_order branches on plan.kind to do the right business logic
         (session-package vs ai_diet_coach).
      3. anything else → default GP subscription processor.
    """
    if any(product_id.startswith(prefix) for prefix in CREDIT_PRODUCT_PREFIXES):
        return {
            "name": "credits",
            "task_name": config.credits_webhook_queue_name,
            "redis_prefix": config.credits_redis_prefix,
            "command_id_prefix": "cr_cmd",
            "command_type": "credits_webhook",
        }

    # Catalog-membership match — covers every plan kind in plans.py
    if get_plan_or_none(product_id) is not None:
        return {
            "name": "nutrition_purchase",
            "task_name": config.gp_nutrition_webhook_queue_name,
            "redis_prefix": config.gp_nutrition_redis_prefix,
            "command_id_prefix": "nutr_cmd",
            "command_type": "nutrition_webhook",
        }

    return {
        "name": "gp_subscription",
        "task_name": config.gp_subscription_webhook_queue_name,
        "redis_prefix": config.gp_subscription_redis_prefix,
        "command_id_prefix": "gpsub_cmd",
        "command_type": "gp_subscription_webhook",
    }


