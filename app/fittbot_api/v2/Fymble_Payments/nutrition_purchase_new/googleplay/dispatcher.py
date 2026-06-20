"""
Queues Google Play nutrition-package commands and dispatches to Celery.

Same pattern as the old dispatcher but with updated payload (no schedule_id/booking_date).
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
from .schemas import NutritionPackagePurchaseRequest, NutritionPackageVerifyRequest


class GooglePlayNutritionPackageDispatcher:

    def __init__(self, store: AsyncCommandStore, config: HighConcurrencyConfig):
        self.store = store
        self.config = config

    async def enqueue_purchase(
        self, payload: NutritionPackagePurchaseRequest, *, client_id: str
    ) -> CommandRecord:
        get_plan(payload.product_sku)
        record = await self.store.create(
            command_type="nutrition_package_purchase",
            payload={
                "client_id": client_id,
                "product_sku": payload.product_sku,
                "currency": payload.currency,
                "os": payload.os,
            },
            idempotency_key=payload.idempotency_key,
            owner_id=client_id,
        )
        self._send_task(self.config.gp_nutrition_purchase_queue_name, record.command_id)
        return record

    async def enqueue_verify(
        self, payload: NutritionPackageVerifyRequest, *, client_id: str
    ) -> CommandRecord:
        record = await self.store.create(
            command_type="nutrition_package_verify",
            payload={"client_id": client_id, "order_id": payload.order_id},
            idempotency_key=payload.idempotency_key,
            owner_id=client_id,
        )
        self._send_task(self.config.gp_nutrition_verify_queue_name, record.command_id)
        return record

    async def enqueue_webhook(
        self, *, signature: str, raw_body: str
    ) -> CommandRecord:
        record = await self.store.create(
            command_type="nutrition_package_webhook",
            payload={"signature": signature, "raw_body": raw_body},
        )
        self._send_task(self.config.gp_nutrition_webhook_queue_name, record.command_id)
        return record

    def _send_task(self, task_name: str, command_id: str) -> None:
        celery_app.send_task(task_name, args=[command_id], queue="payments")
