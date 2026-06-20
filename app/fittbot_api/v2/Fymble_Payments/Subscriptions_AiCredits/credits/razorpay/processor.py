import asyncio
import hashlib
import hmac
import json
import logging
import time
from contextlib import contextmanager
from datetime import timedelta
from typing import Any, Dict, Optional

from redis import Redis
from sqlalchemy.orm import Session

from ..._deps.config import HighConcurrencyConfig, get_payment_settings
from ..._deps.command_store import CommandStore
from ..._deps.database import PaymentDatabase, run_sync_db_operation
from ..._deps.models import (
    CatalogProduct, CreditBalance, CreditLedger,
    Provider, Order, OrderItem, Payment,
)
from ..._deps.sync_service import SubscriptionSyncService
from ..._deps.event_logger import PaymentEventLogger
from ..._deps.utils import now_ist
from app.fittbot_api.v1.payments.razorpay_async_gateway import (
    create_order as rzp_create_order,
    get_payment as rzp_get_payment,
)

from ..shared.credit_service import CreditService, DuplicateGrantError
from app.fittbot_api.v1.client.client_api.reward_program.reward_service import (
    add_ai_scanner_entry_sync,
)
from ..shared.schemas import CreditVerifyResult, CreditWebhookResult
from .schemas import (
    RpCreditCheckoutCommand,
    RpCreditCheckoutResult,
    RpCreditVerifyCommand,
    RpCreditWebhookCommand,
)

logger = logging.getLogger("payments.credits.v2.razorpay.processor")
pel = PaymentEventLogger("razorpay", "food_scanner_credits")

# How many credits each SKU grants — single source of truth.
CREDIT_PACKS: Dict[str, int] = {
    "credit_50": 50,
}

# Redis key patterns (namespaced with rp_ to avoid collision with GP keys)
_CAPTURE_KEY = "credits:rp:capture:{payment_id}"


def _mask(value: str, left: int = 4, right: int = 4) -> str:
    if not value or len(value) <= left + right:
        return "***"
    return value[:left] + "***" + value[-right:]


def _verify_checkout_signature(
    key_secret: str,
    order_id: str,
    payment_id: str,
    signature: str,
) -> bool:
    """Verify Razorpay checkout signature (HMAC-SHA256)."""
    try:
        message = f"{order_id}|{payment_id}".encode("utf-8")
        expected = hmac.new(
            key_secret.encode("utf-8"), message, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False


def _verify_webhook_signature(
    raw_body: str,
    signature: str,
    webhook_secret: str,
) -> bool:
    """Verify Razorpay webhook signature (HMAC-SHA256)."""
    try:
        expected = hmac.new(
            webhook_secret.encode("utf-8"),
            raw_body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False


class RazorpayCreditsProcessor:
    """Background worker for Razorpay credit-purchase lifecycle."""

    def __init__(
        self,
        config: HighConcurrencyConfig,
        payment_db: PaymentDatabase,
        *,
        redis: Optional[Redis] = None,
    ):
        self.config = config
        self.payment_db = payment_db
        self.settings = get_payment_settings()
        self.redis = redis


    async def process_checkout(self, command_id: str, store: CommandStore) -> None:
        record = await store.mark_processing(command_id)
        payload = RpCreditCheckoutCommand(**record.payload)
        _start = time.perf_counter()
        pel.checkout_started(
            command_id=command_id,
            client_id=str(payload.client_id),
            plan_sku=payload.product_sku,
        )
        try:
            result = await self._create_order_and_rzp_checkout(payload)
        except Exception as exc:
            pel.checkout_failed(
                command_id=command_id,
                client_id=str(payload.client_id),
                error_code=type(exc).__name__,
                error_detail=str(exc),
                duration_ms=int((time.perf_counter() - _start) * 1000),
            )
            logger.exception("RP Credits checkout failed: %s", exc)
            await store.mark_failed(command_id, str(exc))
            return


        pel.checkout_completed(
            command_id=command_id,
            client_id=str(payload.client_id),
            duration_ms=int((time.perf_counter() - _start) * 1000),
            plan_sku=payload.product_sku,
        )

        pel.order_created(
            command_id=command_id,
            client_id=str(payload.client_id),
            plan_sku=payload.product_sku,
        )

        await store.mark_completed(command_id, result)

    async def _create_order_and_rzp_checkout(
        self, payload: RpCreditCheckoutCommand
    ) -> Dict[str, Any]:
        with self._session_scope() as session:

            def _op() -> Dict[str, Any]:
                # 1. Fetch catalog product
                product = (
                    session.query(CatalogProduct)
                    .filter(
                        CatalogProduct.sku == payload.product_sku,
                        CatalogProduct.active.is_(True),
                    )
                    .first()
                )
                if not product:
                    raise ValueError("product_not_found")

                credits = CREDIT_PACKS.get(payload.product_sku, 0)
                if credits <= 0:
                    raise ValueError("invalid_credit_pack")

                ist_now = now_ist()
                order_id = (
                    f"ord_{ist_now.strftime('%Y%m%d')}"
                    f"_{payload.client_id}"
                    f"_{int(ist_now.timestamp())}"
                )

                # 2. Create Order + OrderItem in DB
                order = Order(
                    id=order_id,
                    customer_id=payload.client_id,
                    currency=payload.currency,
                    provider=Provider.razorpay_pg.value,
                    gross_amount_minor=product.base_amount_minor,
                    status="pending",
                    order_metadata={
                        "flow": "food_scanner_credits_razorpay",
                    },
                )
                session.add(order)

                sync_service = SubscriptionSyncService(session)
                item_id = sync_service.generate_id("itm")
                order_item = OrderItem(
                    id=item_id,
                    order_id=order_id,
                    item_type="food_scanner_credits",
                    sku=payload.product_sku,
                    title=product.title,
                    unit_price_minor=product.base_amount_minor,
                    qty=1,
                    item_metadata={
                        "credits": credits,
                    },
                )
                session.add(order_item)
                session.commit()

                # Fetch client phone for Razorpay prefill
                from app.models.fittbot_models.client import Client
                client_contact = (
                    session.query(Client.contact)
                    .filter(Client.client_id == int(payload.client_id))
                    .scalar()
                )

                return {
                    "order_id": order_id,
                    "amount_minor": product.base_amount_minor,
                    "currency": payload.currency,
                    "credits": credits,
                    "product_sku": payload.product_sku,
                    "title": product.title,
                    "created_at": order.created_at.isoformat(),
                    "prefill": {
                        "email": "support@fymble.app",
                        "contact": client_contact or "",
                    },
                }

            db_result = await run_sync_db_operation(_op)

        # 3. Create Razorpay order (outside DB transaction)
        pel.provider_call_started(
            command_id=f"rp_cr_checkout_{payload.client_id}",
            provider_endpoint="create_order",
        )
        _prov_start = time.perf_counter()
        try:
            rzp_order = await rzp_create_order(
                amount_minor=db_result["amount_minor"],
                currency=db_result["currency"],
                receipt=db_result["order_id"],
                notes={
                    "flow": "food_scanner_credits_razorpay",
                    "customer_id": payload.client_id,
                    "product_sku": payload.product_sku,
                    "credits": str(db_result["credits"]),
                },
            )
            pel.provider_call_completed(
                command_id=f"rp_cr_checkout_{payload.client_id}",
                provider_endpoint="create_order",
                duration_ms=int((time.perf_counter() - _prov_start) * 1000),
            )
        except Exception as prov_exc:
            pel.provider_call_failed(
                command_id=f"rp_cr_checkout_{payload.client_id}",
                provider_endpoint="create_order",
                error_code=type(prov_exc).__name__,
                duration_ms=int((time.perf_counter() - _prov_start) * 1000),
            )
            raise

        provider_order_id = rzp_order.get("id", "")

        # 4. Update Order with provider_order_id
        with self._session_scope() as session:

            def _update() -> None:
                order = (
                    session.query(Order)
                    .filter(Order.id == db_result["order_id"])
                    .with_for_update()
                    .first()
                )
                if order:
                    order.provider_order_id = provider_order_id
                    session.add(order)
                    session.commit()

            await run_sync_db_operation(_update)

        # 5. Build client checkout response
        settings = self.settings
        return RpCreditCheckoutResult(
            order_id=db_result["order_id"],
            client_id=payload.client_id,
            product_sku=payload.product_sku,
            amount=db_result["amount_minor"],
            currency=db_result["currency"],
            credits=db_result["credits"],
            status="pending",
            key_id=settings.razorpay_key_id,
            provider_order_id=provider_order_id,
            prefill=db_result["prefill"],
            expires_at=(now_ist() + timedelta(minutes=15)).isoformat(),
            created_at=db_result["created_at"],
        ).model_dump()

    # ================================================================
    # VERIFY — signature + capture-marker fast path + DB grant directly
    # (DailyPass-style: the worker does the full work end-to-end.
    #  DB unique constraints + capture marker are the only race guards.)
    # ================================================================

    async def process_verify(self, command_id: str, store: CommandStore) -> None:
        record = await store.mark_processing(command_id)
        payload = RpCreditVerifyCommand(**record.payload)
        customer_id = payload.client_id
        razorpay_payment_id = payload.razorpay_payment_id
        order_id = payload.order_id
        _start = time.perf_counter()

        pel.verify_started(command_id=command_id, client_id=str(customer_id))

        # 1. Validate Razorpay checkout signature
        order_row = self._sync_get_order(order_id)
        if not order_row:
            await store.mark_failed(command_id, "order_not_found")
            return

        sig_valid = _verify_checkout_signature(
            self.settings.razorpay_key_secret,
            order_row.get("provider_order_id", ""),
            razorpay_payment_id,
            payload.razorpay_signature,
        )
        if not sig_valid:
            pel.verify_failed(
                command_id=command_id,
                client_id=str(customer_id),
                error_code="invalid_signature",
                duration_ms=int((time.perf_counter() - _start) * 1000),
            )
            await store.mark_failed(command_id, "invalid_razorpay_signature")
            return

        try:
            # 2. Fast path: capture marker (webhook already wrote credits)
            capture_raw = (
                self.redis.get(_CAPTURE_KEY.format(payment_id=razorpay_payment_id))
                if self.redis
                else None
            )
            if capture_raw:
                fast = self._sync_read_credit_state_by_payment(
                    customer_id, razorpay_payment_id
                )
                if fast:
                    fast["verify_path"] = "capture_marker_instant"
                    pel.verify_completed(
                        command_id=command_id,
                        client_id=str(customer_id),
                        verify_path="capture_marker_instant",
                        duration_ms=int((time.perf_counter() - _start) * 1000),
                    )
                    await store.mark_completed(command_id, fast)
                    return

            # 3. Fast path: existing Payment in DB (webhook may have written
            #    without the marker — e.g. if Redis evicted it)
            if self._sync_check_existing_payment(razorpay_payment_id):
                fast = self._sync_read_credit_state(customer_id, order_id=order_id)
                if fast:
                    fast["verify_path"] = "existing_payment"
                    pel.verify_completed(
                        command_id=command_id,
                        client_id=str(customer_id),
                        verify_path="existing_payment",
                        duration_ms=int((time.perf_counter() - _start) * 1000),
                    )
                    await store.mark_completed(command_id, fast)
                    return

            # 4. Webhook-preferred wait: poll the capture marker for up to 20s
            #    (12 attempts, 600ms → 4s exp backoff). Mirrors the old
            #    DailyPass _await_capture_marker semantics. If the webhook
            #    lands during this window, we read fulfillment from DB and
            #    skip the outbound Razorpay API call.
            awaited = await self._await_capture_marker(razorpay_payment_id)
            if awaited:
                fast = self._sync_read_credit_state_by_payment(
                    customer_id, razorpay_payment_id
                )
                if fast:
                    fast["verify_path"] = "capture_marker_awaited"
                    pel.verify_completed(
                        command_id=command_id,
                        client_id=str(customer_id),
                        verify_path="capture_marker_awaited",
                        duration_ms=int((time.perf_counter() - _start) * 1000),
                    )
                    await store.mark_completed(command_id, fast)
                    return

            # 5. Webhook never arrived in time → fall back to Razorpay API.
            #    DB unique constraints arbitrate any race with a late webhook.
            result = await self._verify_via_razorpay(
                customer_id, razorpay_payment_id, order_id
            )
        except Exception as exc:
            pel.verify_failed(
                command_id=command_id,
                client_id=str(customer_id),
                error_code=type(exc).__name__,
                error_detail=str(exc),
                duration_ms=int((time.perf_counter() - _start) * 1000),
            )
            logger.exception("Credits RP verify failed: %s", exc)
            await store.mark_failed(command_id, str(exc))
            return

        _dur = int((time.perf_counter() - _start) * 1000)
        if result.get("verified"):
            result["verify_path"] = "razorpay_api_fallback"
            pel.verify_completed(
                command_id=command_id,
                client_id=str(customer_id),
                verify_path="razorpay_api_fallback",
                duration_ms=_dur,
            )
            pel.payment_captured(
                command_id=command_id, client_id=str(customer_id)
            )
            await store.mark_completed(command_id, result)
        else:
            pel.verify_failed(
                command_id=command_id,
                client_id=str(customer_id),
                error_code="not_verified",
                duration_ms=_dur,
                error_detail=result.get("message"),
            )
            await store.mark_failed(
                command_id, result.get("message", "verify_unsuccessful")
            )

    async def _verify_via_razorpay(
        self,
        customer_id: str,
        razorpay_payment_id: str,
        order_id: Optional[str],
    ) -> Dict[str, Any]:
        """Fallback: call Razorpay API to confirm payment status, then grant credits."""

        # 1. Fetch payment from Razorpay
        pel.provider_call_started(
            command_id=f"rp_cr_verify_{customer_id}",
            provider_endpoint="get_payment",
        )
        _prov_start = time.perf_counter()
        try:
            payment_data = await rzp_get_payment(razorpay_payment_id)
            pel.provider_call_completed(
                command_id=f"rp_cr_verify_{customer_id}",
                provider_endpoint="get_payment",
                duration_ms=int((time.perf_counter() - _prov_start) * 1000),
            )
        except Exception as prov_exc:
            pel.provider_call_failed(
                command_id=f"rp_cr_verify_{customer_id}",
                provider_endpoint="get_payment",
                error_code=type(prov_exc).__name__,
                duration_ms=int((time.perf_counter() - _prov_start) * 1000),
            )
            raise

        rzp_status = payment_data.get("status", "")
        if rzp_status != "captured":
            return CreditVerifyResult(
                verified=False,
                captured=False,
                message=f"Payment not captured (status={rzp_status})",
                order_id=order_id,
            ).model_dump()

        # 2. Determine credits from order
        with self._session_scope() as session:

            def _op() -> Dict[str, Any]:
                credits_to_grant = 0
                product_sku = None

                if order_id:
                    items = (
                        session.query(OrderItem)
                        .filter(OrderItem.order_id == order_id)
                        .all()
                    )
                    for item in items:
                        if item.item_type == "food_scanner_credits":
                            meta = item.item_metadata or {}
                            credits_to_grant = meta.get("credits", 0)
                            product_sku = item.sku
                            break

                if credits_to_grant <= 0:
                    product_sku = product_sku or "credit_50"
                    credits_to_grant = CREDIT_PACKS.get(product_sku, 50)

                # Update order status
                if order_id:
                    pending_order = (
                        session.query(Order)
                        .filter(
                            Order.id == order_id,
                            Order.status == "pending",
                        )
                        .with_for_update()
                        .first()
                    )
                    if pending_order:
                        pending_order.status = "paid"
                        session.add(pending_order)

                # Create Payment (idempotent check by provider_payment_id)
                existing_payment = (
                    session.query(Payment)
                    .filter(Payment.provider_payment_id == razorpay_payment_id)
                    .first()
                )
                if existing_payment:
                    # Already processed — read balance and return
                    credit_svc = CreditService(session)
                    bal = credit_svc.get_balance(int(customer_id))
                    session.commit()
                    return CreditVerifyResult(
                        verified=True,
                        captured=True,
                        message="Credits already granted",
                        order_id=order_id,
                        payment_id=existing_payment.id,
                        credits_granted=credits_to_grant,
                        new_balance=bal.balance,
                    ).model_dump()

                sync_service = SubscriptionSyncService(session)
                payment_id = sync_service.generate_id("pay")
                payment = Payment(
                    id=payment_id,
                    order_id=order_id,
                    customer_id=customer_id,
                    provider=Provider.razorpay_pg.value,
                    provider_payment_id=razorpay_payment_id,
                    amount_minor=int(payment_data.get("amount", 0)),
                    currency=payment_data.get("currency", "INR"),
                    status="captured",
                    captured_at=now_ist(),
                    payment_metadata={
                        "source": "verify_fallback",
                        "flow": "food_scanner_credits_razorpay",
                        "verified_at": now_ist().isoformat(),
                    },
                )
                session.add(payment)

                # Grant credits (idempotent)
                credit_svc = CreditService(session)
                grant_source_id = order_id or razorpay_payment_id
                try:
                    new_balance = credit_svc.grant_credits(
                        client_id=int(customer_id),
                        credits=credits_to_grant,
                        txn_type="purchase",
                        source_order_id=grant_source_id,
                        description=f"Purchased {product_sku} ({credits_to_grant} credits) via Razorpay",
                    )
                except DuplicateGrantError:
                    bal = credit_svc._lock_or_create_balance(int(customer_id))
                    new_balance = bal.balance

                # Grant 1 ai_scanner reward entry (idempotent on source_id)
                try:
                    add_ai_scanner_entry_sync(
                        session=session,
                        client_id=int(customer_id),
                        source_id=grant_source_id,
                    )
                except Exception as reward_exc:
                    logger.warning(
                        "CREDITS_RP_REWARD_ENTRY_ERROR | order=%s sku=%s error=%s",
                        grant_source_id, product_sku, reward_exc,
                    )

                session.commit()

                return CreditVerifyResult(
                    verified=True,
                    captured=True,
                    message="Credits purchased successfully",
                    order_id=order_id,
                    payment_id=payment_id,
                    credits_granted=credits_to_grant,
                    new_balance=new_balance,
                ).model_dump()

            return await run_sync_db_operation(_op)

    # ================================================================
    # WEBHOOK — payment.captured -> grant credits + complete pending verify
    # ================================================================

    async def process_webhook(self, command_id: str, store: CommandStore) -> None:
        record = await store.mark_processing(command_id)
        payload = RpCreditWebhookCommand(**record.payload)
        _start = time.perf_counter()
        pel.webhook_received(command_id=command_id)
        try:
            result = await self._handle_webhook(
                payload.raw_body, payload.razorpay_signature
            )
        except Exception as exc:
            pel.webhook_failed(
                command_id=command_id,
                error_code=type(exc).__name__,
                duration_ms=int((time.perf_counter() - _start) * 1000),
                error_detail=str(exc),
            )
            logger.exception("Credits RP webhook failed: %s", exc)
            await store.mark_failed(command_id, str(exc))
            return

        pel.webhook_processed(
            command_id=command_id,
            duration_ms=int((time.perf_counter() - _start) * 1000),
            event_type=result.get("event_type"),
            status=result.get("status"),
        )
        await store.mark_completed(command_id, result)

    async def _handle_webhook(
        self, raw_body: str, razorpay_signature: str
    ) -> Dict[str, Any]:
        # 1. Validate webhook signature
        if not _verify_webhook_signature(
            raw_body, razorpay_signature, self.settings.razorpay_webhook_secret
        ):
            pel.webhook_signature_invalid(command_id="credits_rp_webhook")
            raise ValueError("invalid_webhook_signature")

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid_json: {exc}") from exc

        event_type = payload.get("event", "")
        payment_entity = (
            payload.get("payload", {}).get("payment", {}).get("entity", {})
        )
        razorpay_payment_id = payment_entity.get("id", "")
        razorpay_order_id = payment_entity.get("order_id", "")
        notes = payment_entity.get("notes", {})
        flow = notes.get("flow", "")

        # Only handle food_scanner_credits_razorpay flow
        if flow != "food_scanner_credits_razorpay":
            return CreditWebhookResult(
                status="skipped",
                event_type=event_type,
                reason="not_a_credit_purchase",
            ).model_dump()

        if event_type == "payment.captured":
            return await self._webhook_grant_credits(
                payment_entity, razorpay_payment_id, razorpay_order_id, notes
            )
        elif event_type == "payment.failed":
            return await self._webhook_payment_failed(
                razorpay_order_id, razorpay_payment_id
            )
        elif event_type in ("refund.created", "refund.processed"):
            refund_entity = (
                payload.get("payload", {}).get("refund", {}).get("entity", {})
            )
            return await self._webhook_refund_credits(
                refund_entity, payment_entity, notes
            )
        else:
            return CreditWebhookResult(
                status="ignored",
                event_type=event_type,
            ).model_dump()

    async def _webhook_grant_credits(
        self,
        payment_entity: dict,
        razorpay_payment_id: str,
        razorpay_order_id: str,
        notes: dict,
    ) -> Dict[str, Any]:
        customer_id = notes.get("customer_id", "")
        product_sku = notes.get("product_sku", "credit_50")
        credits_to_grant = int(notes.get("credits", 0)) or CREDIT_PACKS.get(
            product_sku, 50
        )

        with self._session_scope() as session:

            def _op() -> Dict[str, Any]:
                sync_service = SubscriptionSyncService(session)

                # Find matching order by provider_order_id
                order = (
                    session.query(Order)
                    .filter(Order.provider_order_id == razorpay_order_id)
                    .with_for_update()
                    .first()
                )

                if order and order.status == "pending":
                    order.status = "paid"
                    session.add(order)

                order_id = order.id if order else None

                # Idempotency: check existing Payment
                existing_payment = (
                    session.query(Payment)
                    .filter(Payment.provider_payment_id == razorpay_payment_id)
                    .first()
                )
                if existing_payment:
                    credit_svc = CreditService(session)
                    bal = credit_svc.get_balance(int(customer_id))
                    session.commit()
                    return CreditWebhookResult(
                        status="processed",
                        event_type="payment.captured",
                        credits_granted=credits_to_grant,
                        new_balance=bal.balance,
                        order_id=order_id,
                        payment_id=existing_payment.id,
                    ).model_dump()

                payment_id = sync_service.generate_id("pay")
                payment = Payment(
                    id=payment_id,
                    order_id=order_id,
                    customer_id=customer_id,
                    provider=Provider.razorpay_pg.value,
                    provider_payment_id=razorpay_payment_id,
                    amount_minor=int(payment_entity.get("amount", 0)),
                    currency=payment_entity.get("currency", "INR"),
                    status="captured",
                    captured_at=now_ist(),
                    payment_metadata={
                        "source": "webhook",
                        "flow": "food_scanner_credits_razorpay",
                        "event_type": "payment.captured",
                    },
                )
                session.add(payment)

                credit_svc = CreditService(session)
                grant_source_id = order_id or razorpay_payment_id
                try:
                    new_balance = credit_svc.grant_credits(
                        client_id=int(customer_id),
                        credits=credits_to_grant,
                        txn_type="purchase",
                        source_order_id=grant_source_id,
                        description=f"Webhook: {product_sku} ({credits_to_grant} credits) via Razorpay",
                    )
                except DuplicateGrantError:
                    bal = credit_svc._lock_or_create_balance(int(customer_id))
                    new_balance = bal.balance

                # Grant 1 ai_scanner reward entry (idempotent on source_id)
                try:
                    add_ai_scanner_entry_sync(
                        session=session,
                        client_id=int(customer_id),
                        source_id=grant_source_id,
                    )
                except Exception as reward_exc:
                    logger.warning(
                        "CREDITS_RP_WEBHOOK_REWARD_ENTRY_ERROR | order=%s sku=%s error=%s",
                        grant_source_id, product_sku, reward_exc,
                    )

                session.commit()
                return CreditWebhookResult(
                    status="processed",
                    event_type="payment.captured",
                    credits_granted=credits_to_grant,
                    new_balance=new_balance,
                    order_id=order_id,
                    payment_id=payment_id,
                ).model_dump()

            result = await run_sync_db_operation(_op)

        # Post-DB: set capture marker so any pending verify hits the fast path.
        if self.redis and razorpay_payment_id:
            self.redis.set(
                _CAPTURE_KEY.format(payment_id=razorpay_payment_id),
                json.dumps({
                    "event_type": "payment.captured",
                    "credits": credits_to_grant,
                    "granted_at": now_ist().isoformat(),
                }),
                ex=self.config.rp_credits_capture_cache_ttl_seconds,
            )
        return result

    # ================================================================
    # WEBHOOK — payment.failed -> mark order failed
    # ================================================================

    async def _webhook_payment_failed(
        self, razorpay_order_id: str, razorpay_payment_id: str
    ) -> Dict[str, Any]:
        with self._session_scope() as session:

            def _op() -> Dict[str, Any]:
                order = (
                    session.query(Order)
                    .filter(Order.provider_order_id == razorpay_order_id)
                    .with_for_update()
                    .first()
                )
                if order and order.status == "pending":
                    order.status = "failed"
                    session.add(order)
                session.commit()
                return CreditWebhookResult(
                    status="processed",
                    event_type="payment.failed",
                    order_id=order.id if order else None,
                ).model_dump()

            return await run_sync_db_operation(_op)

    # ================================================================
    # WEBHOOK — refund.created / refund.processed -> deduct credits
    # ================================================================

    async def _webhook_refund_credits(
        self,
        refund_entity: dict,
        payment_entity: dict,
        notes: dict,
    ) -> Dict[str, Any]:
        customer_id = notes.get("customer_id", "")
        product_sku = notes.get("product_sku", "credit_50")
        credits_to_refund = int(notes.get("credits", 0)) or CREDIT_PACKS.get(
            product_sku, 50
        )

        # Fallback: if notes are missing, look up Payment to find customer/order
        razorpay_payment_id = (
            refund_entity.get("payment_id") or payment_entity.get("id") or ""
        )

        with self._session_scope() as session:

            def _op() -> Dict[str, Any]:
                # Resolve customer via Payment if notes were empty
                resolved_customer = customer_id
                source_order_id = None
                if not resolved_customer and razorpay_payment_id:
                    pay_row = (
                        session.query(Payment)
                        .filter(
                            Payment.provider_payment_id == razorpay_payment_id
                        )
                        .first()
                    )
                    if pay_row:
                        resolved_customer = pay_row.customer_id
                        source_order_id = pay_row.order_id

                if not resolved_customer:
                    return CreditWebhookResult(
                        status="skipped",
                        event_type="refund",
                        reason="customer_unresolved",
                    ).model_dump()

                client_id_int = int(resolved_customer)
                credit_svc = CreditService(session)
                bal = credit_svc.get_balance(client_id_int)
                actual_refund = min(credits_to_refund, bal.balance)
                new_balance = bal.balance - actual_refund

                if actual_refund > 0:
                    credit_svc.deduct_credit(
                        client_id_int,
                        amount=actual_refund,
                        description=f"Refund: {product_sku} (-{actual_refund} credits) via Razorpay",
                    )
                    latest = (
                        session.query(CreditLedger)
                        .filter(CreditLedger.client_id == client_id_int)
                        .order_by(CreditLedger.created_at.desc())
                        .first()
                    )
                    if latest:
                        latest.txn_type = "refunded"
                        if source_order_id:
                            latest.source_order_id = source_order_id
                        session.add(latest)

                session.commit()
                return CreditWebhookResult(
                    status="processed",
                    event_type="refund",
                    credits_refunded=actual_refund,
                    new_balance=new_balance,
                ).model_dump()

            return await run_sync_db_operation(_op)

    # ── Sync helpers ────────────────────────────────────────────────

    def _sync_get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Read order from DB (sync). Returns dict with provider_order_id."""
        with self._session_scope() as session:
            order = session.query(Order).filter(Order.id == order_id).first()
            if not order:
                return None
            return {
                "id": order.id,
                "provider_order_id": order.provider_order_id,
                "customer_id": order.customer_id,
                "status": order.status,
                "gross_amount_minor": order.gross_amount_minor,
            }

    def _sync_check_existing_payment(
        self, razorpay_payment_id: str
    ) -> Optional[Dict[str, Any]]:
        with self._session_scope() as session:
            payment = (
                session.query(Payment)
                .filter(Payment.provider_payment_id == razorpay_payment_id)
                .first()
            )
            if not payment:
                return None
            return {"id": payment.id, "order_id": payment.order_id}

    def _sync_read_credit_state(
        self, customer_id: str, *, order_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        with self._session_scope() as session:
            client_id_int = int(customer_id)
            query = session.query(CreditLedger).filter(
                CreditLedger.client_id == client_id_int,
                CreditLedger.txn_type == "purchase",
            )
            if order_id:
                query = query.filter(CreditLedger.source_order_id == order_id)
            ledger = query.order_by(CreditLedger.created_at.desc()).first()
            if not ledger:
                return None
            bal = (
                session.query(CreditBalance)
                .filter(CreditBalance.client_id == client_id_int)
                .first()
            )
            return CreditVerifyResult(
                verified=True,
                captured=True,
                message="Credits verified",
                order_id=ledger.source_order_id,
                credits_granted=ledger.credits,
                new_balance=bal.balance if bal else ledger.balance_after,
            ).model_dump()

    def _sync_read_credit_state_by_payment(
        self, customer_id: str, razorpay_payment_id: str
    ) -> Optional[Dict[str, Any]]:
        """Read credit state using Payment's provider_payment_id."""
        with self._session_scope() as session:
            payment = (
                session.query(Payment)
                .filter(Payment.provider_payment_id == razorpay_payment_id)
                .first()
            )
            if not payment:
                return None
            return self._sync_read_credit_state(
                customer_id, order_id=payment.order_id
            )

    # ── Webhook-preferred capture-marker polling ─────────────────────
    # Mirrors v1 DailyPass _await_capture_marker semantics: poll the
    # Razorpay-payment-keyed capture marker with exponential backoff so
    # verify defers to the webhook when it arrives within the window.

    def _capture_marker_snapshot(
        self, razorpay_payment_id: str
    ) -> Optional[Dict[str, Any]]:
        if not self.redis or not razorpay_payment_id:
            return None
        raw = self.redis.get(_CAPTURE_KEY.format(payment_id=razorpay_payment_id))
        if not raw:
            return None
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            return json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            return None

    def _marker_present(self, razorpay_payment_id: str) -> bool:
        return self._capture_marker_snapshot(razorpay_payment_id) is not None

    async def _await_capture_marker(
        self, razorpay_payment_id: str,
    ) -> Optional[Dict[str, Any]]:
        if not self.redis or not razorpay_payment_id:
            return None

        # Match v1 dailypass defaults.
        deadline_seconds = max(
            1,
            getattr(
                self.config, "rp_credits_verify_total_timeout_seconds", 20
            ),
        )
        attempts = 12
        delay = 0.6
        max_delay = 4.0

        deadline = time.monotonic() + deadline_seconds
        for _ in range(attempts):
            marker = await asyncio.to_thread(
                self._capture_marker_snapshot, razorpay_payment_id
            )
            if marker:
                return marker
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(delay)
            delay = min(max_delay, delay * 1.5)
        return None

    @contextmanager
    def _session_scope(self):
        with self.payment_db.get_session() as session:
            yield session
