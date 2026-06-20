

from app.celery_app import celery_app
from ..._deps.config import HighConcurrencyConfig

from ...shared.async_command_store import AsyncCommandStore, CommandRecord
from .schemas import CreditPurchaseRequest, CreditVerifyRequest


class GooglePlayCreditsDispatcher:
    """Queues Google Play credit-purchase commands and hands them to Celery."""

    def __init__(self, store: AsyncCommandStore, config: HighConcurrencyConfig):
        self.store = store
        self.config = config

    async def enqueue_purchase(
        self, payload: CreditPurchaseRequest, *, client_id: str
    ) -> CommandRecord:
        record = await self.store.create(
            command_type="credits_purchase",
            payload={
                "client_id": client_id,
                "product_sku": payload.product_sku,
                "currency": payload.currency,
                "os": payload.os,
            },
            idempotency_key=payload.idempotency_key,
            owner_id=client_id,
        )
        self._send_task(self.config.credits_purchase_queue_name, record.command_id)
        return record

    async def enqueue_verify(
        self, payload: CreditVerifyRequest, *, client_id: str
    ) -> CommandRecord:
        record = await self.store.create(
            command_type="credits_verify",
            payload={"client_id": client_id, "order_id": payload.order_id},
            idempotency_key=payload.idempotency_key,
            owner_id=client_id,
        )
        self._send_task(self.config.credits_verify_queue_name, record.command_id)
        return record

    async def enqueue_webhook(
        self, *, signature: str, raw_body: str
    ) -> CommandRecord:
        record = await self.store.create(
            command_type="credits_webhook",
            payload={"signature": signature, "raw_body": raw_body},
        )
        self._send_task(self.config.credits_webhook_queue_name, record.command_id)
        return record

    # ── Internal ────────────────────────────────────────────────────

    def _send_task(self, task_name: str, command_id: str) -> None:
        celery_app.send_task(task_name, args=[command_id], queue="payments")
