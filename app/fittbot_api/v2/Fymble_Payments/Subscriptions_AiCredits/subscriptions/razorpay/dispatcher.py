"""
Dispatcher: translates HTTP payloads into persistent commands + fans out to Celery.
"""

from typing import Dict

from app.celery_app import celery_app
from ..._deps.config import HighConcurrencyConfig

from ...shared.async_command_store import AsyncCommandStore, CommandRecord
from .schemas import RpSubscriptionCheckoutRequest, RpSubscriptionVerifyRequest


class RazorpaySubscriptionDispatcher:
    """Queues Razorpay subscription commands and hands them to Celery."""

    def __init__(self, store: AsyncCommandStore, config: HighConcurrencyConfig):
        self.store = store
        self.config = config

    async def enqueue_checkout(
        self, payload: RpSubscriptionCheckoutRequest, *, user_id: str
    ) -> CommandRecord:
        record = await self.store.create(
            command_type="rp_subscription_checkout",
            payload={
                "user_id": user_id,
                "plan_sku": payload.plan_sku,
                "metadata": payload.metadata,
            },
            idempotency_key=payload.idempotency_key,
            owner_id=user_id,
        )
        self._send_task(self.config.rp_subscription_checkout_queue_name, record.command_id)
        return record

    async def enqueue_verify(
        self, payload: RpSubscriptionVerifyRequest, *, user_id: str
    ) -> CommandRecord:
        record = await self.store.create(
            command_type="rp_subscription_verify",
            payload={
                "user_id": user_id,
                "razorpay_payment_id": payload.razorpay_payment_id,
                "razorpay_subscription_id": payload.razorpay_subscription_id,
                "razorpay_signature": payload.razorpay_signature,
            },
            idempotency_key=payload.idempotency_key,
            owner_id=user_id,
        )
        self._send_task(self.config.rp_subscription_verify_queue_name, record.command_id)
        return record

    async def enqueue_webhook(
        self, *, raw_body: str, signature: str, webhook_id: str = ""
    ) -> CommandRecord:
        record = await self.store.create(
            command_type="rp_subscription_webhook",
            payload={
                "raw_body": raw_body,
                "signature": signature,
                "webhook_id": webhook_id,
            },
        )
        self._send_task(self.config.rp_subscription_webhook_queue_name, record.command_id)
        return record

    # ── Internal ────────────────────────────────────────────────────

    def _send_task(self, task_name: str, command_id: str) -> None:
        celery_app.send_task(task_name, args=[command_id], queue="payments")
