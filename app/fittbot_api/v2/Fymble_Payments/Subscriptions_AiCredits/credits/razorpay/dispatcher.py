

from app.celery_app import celery_app
from ..._deps.config import HighConcurrencyConfig

from ...shared.async_command_store import AsyncCommandStore, CommandRecord
from .schemas import RpCreditCheckoutRequest, RpCreditVerifyRequest


class RazorpayCreditsDispatcher:
    """Queues Razorpay credit-purchase commands and hands them to Celery."""

    def __init__(self, store: AsyncCommandStore, config: HighConcurrencyConfig):
        self.store = store
        self.config = config

    async def enqueue_checkout(
        self, payload: RpCreditCheckoutRequest, *, client_id: str
    ) -> CommandRecord:
        record = await self.store.create(
            command_type="rp_credits_checkout",
            payload={
                "client_id": client_id,
                "product_sku": payload.product_sku,
                "currency": payload.currency,
            },
            idempotency_key=payload.idempotency_key,
            owner_id=client_id,
        )
        self._send_task(
            self.config.rp_credits_checkout_queue_name, record.command_id
        )
        return record

    async def enqueue_verify(
        self, payload: RpCreditVerifyRequest, *, client_id: str
    ) -> CommandRecord:
        record = await self.store.create(
            command_type="rp_credits_verify",
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
            self.config.rp_credits_verify_queue_name, record.command_id
        )
        return record

    async def enqueue_webhook(
        self, *, raw_body: str, razorpay_signature: str
    ) -> CommandRecord:
        record = await self.store.create(
            command_type="rp_credits_webhook",
            payload={
                "raw_body": raw_body,
                "razorpay_signature": razorpay_signature,
            },
        )
        self._send_task(
            self.config.rp_credits_webhook_queue_name, record.command_id
        )
        return record

    # ── Internal ────────────────────────────────────────────────────

    def _send_task(self, task_name: str, command_id: str) -> None:
        celery_app.send_task(task_name, args=[command_id], queue="payments")
