"""
Queues Razorpay nutrition-package commands and dispatches them to Celery.
"""

from app.celery_app import celery_app
from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits._deps.config import (
    HighConcurrencyConfig,
)
from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits.shared.async_command_store import (
    AsyncCommandStore,
    CommandRecord,
)
from ..plans import get_plan
from .schemas import (
    RpNutritionPackageCheckoutRequest,
    RpNutritionPackageVerifyRequest,
)


class RazorpayNutritionPackageDispatcher:
    """Queues Razorpay nutrition-package commands and hands them to Celery."""

    def __init__(self, store: AsyncCommandStore, config: HighConcurrencyConfig):
        self.store = store
        self.config = config

    async def enqueue_checkout(
        self, payload: RpNutritionPackageCheckoutRequest, *, client_id: str
    ) -> CommandRecord:
        get_plan(payload.product_sku)
        cmd_payload = {
            "client_id": client_id,
            "product_sku": payload.product_sku,
            "currency": payload.currency,
        }
        if payload.booking is not None:
            cmd_payload["booking"] = payload.booking.model_dump()
        record = await self.store.create(
            command_type="rp_nutrition_pkg_checkout",
            payload=cmd_payload,
            idempotency_key=payload.idempotency_key,
            owner_id=client_id,
        )
        self._send_task(
            self.config.rp_nutrition_pkg_checkout_queue_name, record.command_id
        )
        return record

    async def enqueue_verify(
        self, payload: RpNutritionPackageVerifyRequest, *, client_id: str
    ) -> CommandRecord:
        record = await self.store.create(
            command_type="rp_nutrition_pkg_verify",
            payload={
                "client_id": client_id,
                "order_id": payload.order_id,
                "razorpay_payment_id": payload.razorpay_payment_id,
                "razorpay_signature": payload.razorpay_signature,
            },
            idempotency_key=payload.idempotency_key,
            owner_id=client_id,
        )
        self._send_task(
            self.config.rp_nutrition_pkg_verify_queue_name, record.command_id
        )
        return record

    async def enqueue_webhook(
        self, *, raw_body: str, razorpay_signature: str
    ) -> CommandRecord:
        record = await self.store.create(
            command_type="rp_nutrition_pkg_webhook",
            payload={
                "raw_body": raw_body,
                "razorpay_signature": razorpay_signature,
            },
        )
        self._send_task(
            self.config.rp_nutrition_pkg_webhook_queue_name, record.command_id
        )
        return record

    def _send_task(self, task_name: str, command_id: str) -> None:
        celery_app.send_task(task_name, args=[command_id], queue="payments")
