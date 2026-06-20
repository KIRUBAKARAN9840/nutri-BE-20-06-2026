import asyncio
import json
import logging
import time
from contextlib import contextmanager
from datetime import timedelta
from typing import Any, Dict, Optional

from redis import Redis
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from ..._deps.config import HighConcurrencyConfig, get_payment_settings
from ..._deps.command_store import CommandStore
from ..._deps.database import PaymentDatabase, run_sync_db_operation
from ..._deps.models import (
    CatalogProduct, CreditBalance, CreditLedger,
    Provider, Order, OrderItem, Payment,
)
from ..._deps.sync_service import SubscriptionSyncService
from ..._deps.revenuecat import RevenueCatAPIError, get_subscriber as rc_get_subscriber
from ..._deps.event_logger import PaymentEventLogger
from ..._deps.utils import now_ist

from ..shared.credit_service import CreditService, DuplicateGrantError
from app.fittbot_api.v1.client.client_api.reward_program.reward_service import (
    add_ai_scanner_entry_sync,
)
from ..shared.schemas import CreditVerifyResult, CreditWebhookResult
from .schemas import (
    CreditPurchaseCommand,
    CreditPurchaseResult,
    CreditVerifyCommand,
    CreditWebhookCommand,
)

logger = logging.getLogger("payments.credits.v2.googleplay.processor")
pel = PaymentEventLogger("revenuecat", "food_scanner_credits")


CREDIT_PACKS: Dict[str, int] = {
    "credit_50": 50,
    "credit_99": 50,
}

# Time-boxed "unlimited scans" passes. These do NOT add to the credit
# balance — they set CreditBalance.unlimited_until so per-scan deduction
# is skipped while active.
SCAN_PASSES: Dict[str, timedelta] = {
    "credit_999": timedelta(days=365),
}

# Every SKU we treat as a food-scanner product (packs + passes).
ALL_CREDIT_SKUS = set(CREDIT_PACKS) | set(SCAN_PASSES)


def _match_sku(product_id: str) -> Optional[str]:
    """Resolve a RevenueCat product_id string to a known SKU.

    Longest SKU first so "credit_999" wins over its substring "credit_99".
    """
    for known in sorted(ALL_CREDIT_SKUS, key=len, reverse=True):
        if known in product_id:
            return known
    return None


_CAPTURE_KEY = "credits:gp:capture:{customer_id}:{order_id}"
_VERIFY_DONE_KEY = "credits:gp:verify_done:{command_id}"
_PENDING_VERIFY_KEY = "credits:gp:pending_verify:{customer_id}"


def _mask(value: str, left: int = 4, right: int = 4) -> str:
    if not value or len(value) <= left + right:
        return "***"
    return value[:left] + "***" + value[-right:]


class GooglePlayCreditsProcessor:
    """Background worker for Google Play (RevenueCat) credit-purchase lifecycle."""

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


    async def process_purchase(self, command_id: str, store: CommandStore) -> None:
        record = await store.mark_processing(command_id)
        payload = CreditPurchaseCommand(**record.payload)
        _start = time.perf_counter()
        pel.checkout_started(
            command_id=command_id,
            client_id=str(payload.client_id),
            plan_sku=payload.product_sku,
        )
        try:
            result = await self._create_pending_order(payload)
        except Exception as exc:
            pel.checkout_failed(
                command_id=command_id,
                client_id=str(payload.client_id),
                error_code=type(exc).__name__,
                error_detail=str(exc),
                duration_ms=int((time.perf_counter() - _start) * 1000),
            )
            logger.exception("Credits purchase command failed: %s", exc)
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

    async def _create_pending_order(
        self, payload: CreditPurchaseCommand
    ) -> Dict[str, Any]:
        with self._session_scope() as session:

            def _op() -> Dict[str, Any]:
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

                ist_now = now_ist()
                order_id = (
                    f"ord_{ist_now.strftime('%Y%m%d')}"
                    f"_{payload.client_id}"
                    f"_{int(ist_now.timestamp())}"
                )

                order = Order(
                    id=order_id,
                    customer_id=payload.client_id,
                    currency=payload.currency,
                    provider=Provider.google_play.value,
                    gross_amount_minor=product.base_amount_minor,
                    status="pending",
                    order_metadata={"flow": "food_scanner_credits"},
                )
                session.add(order)

                item_metadata = {
                    "credits": CREDIT_PACKS.get(payload.product_sku, 0),
                }
                if payload.product_sku in SCAN_PASSES:
                    item_metadata["scan_pass_days"] = SCAN_PASSES[
                        payload.product_sku
                    ].days

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
                    item_metadata=item_metadata,
                )
                session.add(order_item)
                session.commit()

                api_key = (
                    self.settings.revenuecat_api_key_ios
                    if payload.os == "ios"
                    else self.settings.revenuecat_api_key
                )

                return CreditPurchaseResult(
                    order_id=order.id,
                    client_id=payload.client_id,
                    product_sku=payload.product_sku,
                    amount=product.base_amount_minor,
                    currency=payload.currency,
                    credits=CREDIT_PACKS.get(payload.product_sku, 0),
                    status="pending",
                    api_key=api_key,
                    expires_at=(now_ist() + timedelta(minutes=15)).isoformat(),
                    created_at=order.created_at.isoformat(),
                ).model_dump()

            return await run_sync_db_operation(_op)

    # ================================================================
    # VERIFY — instant check -> pending_webhook + schedule fallback
    # ================================================================

    async def process_verify(self, command_id: str, store: CommandStore) -> None:
        """
        Phase 1 of verify: one quick Redis check, then exit.
        Worker freed in ~100ms. No sleeping.
        """
        record = await store.mark_processing(command_id)
        payload = CreditVerifyCommand(**record.payload)
        customer_id = payload.client_id
        order_id = payload.order_id

        pel.verify_started(command_id=command_id, client_id=str(customer_id))

        # Quick check: capture marker for THIS specific order in Redis?
        capture_key = _CAPTURE_KEY.format(customer_id=customer_id, order_id=order_id)
        capture_raw = self.redis.get(capture_key) if self.redis else None
        capture_marker = json.loads(capture_raw) if capture_raw else None
        if capture_marker:
            result = self._sync_read_credit_state(customer_id, order_id=order_id)
            if result:
                result["verify_path"] = "capture_marker_instant"
                pel.verify_completed(
                    command_id=command_id,
                    client_id=str(customer_id),
                    verify_path="capture_marker_instant",
                    duration_ms=0,
                )
                await store.mark_completed(command_id, result)
                return

        # No marker yet — mark as pending_webhook
        if self.redis:
            pending_key = _PENDING_VERIFY_KEY.format(customer_id=customer_id)
            self.redis.set(
                pending_key,
                json.dumps({
                    "command_id": command_id,
                    "customer_id": customer_id,
                }),
                ex=30,
            )

        # Write pending_webhook status directly
        record = await store.get(command_id)
        if record and self.redis:
            data = {
                "command_id": record.command_id,
                "command_type": record.command_type,
                "status": "pending_webhook",
                "payload": record.payload,
                "owner_id": record.owner_id,
                "result": None,
                "error": None,
                "created_at": record.created_at,
                "updated_at": int(time.time()),
            }
            redis_key = store._key(command_id)
            self.redis.set(
                redis_key,
                json.dumps(data),
                ex=self.config.command_ttl_seconds,
            )

        # Schedule fallback task with 20s countdown
        celery_app.send_task(
            self.config.credits_verify_fallback_queue_name,
            args=[command_id],
            queue="payments",
            countdown=self.config.credits_verify_total_timeout_seconds,
        )

        logger.info(
            "CREDITS_GP_VERIFY_PENDING_WEBHOOK | customer=%s command=%s fallback_in=%ds",
            _mask(customer_id),
            command_id,
            self.config.credits_verify_total_timeout_seconds,
        )
        # Worker freed here — no sleeping!

    # ================================================================
    # VERIFY FALLBACK — fires after 20s if webhook didn't complete it
    # ================================================================

    async def process_verify_fallback(
        self, command_id: str, store: CommandStore
    ) -> None:
        # Layer 1: done-flag (set by webhook when it completes the command)
        if self.redis:
            done_key = _VERIFY_DONE_KEY.format(command_id=command_id)
            done_flag = self.redis.get(done_key)
            if done_flag:
                logger.info(
                    "CREDITS_GP_FALLBACK_SKIPPED | command=%s (done flag set)",
                    command_id,
                )
                return

        # Layer 2: re-read current status from Redis
        current_status = self._sync_read_command_status(store, command_id)
        if current_status == "completed":
            logger.info(
                "CREDITS_GP_FALLBACK_ALREADY_COMPLETED | command=%s",
                command_id,
            )
            return
        if current_status not in ("pending_webhook", "processing", "queued"):
            logger.info(
                "CREDITS_GP_FALLBACK_UNEXPECTED_STATUS | command=%s status=%s",
                command_id,
                current_status,
            )
            return

        # Read payload for customer_id
        record = await store.get(command_id)
        if not record:
            logger.warning("CREDITS_GP_FALLBACK_COMMAND_NOT_FOUND | command=%s", command_id)
            return

        customer_id = record.payload.get("client_id")
        if not customer_id:
            self._sync_write_command_status(
                store, command_id, record, "failed", error="missing_client_id"
            )
            return

        order_id = record.payload.get("order_id")

        _start = time.perf_counter()
        pel.verify_started(command_id=command_id, client_id=str(customer_id))

        try:
            result = await self._verify_via_revenuecat(customer_id, order_id)
        except Exception as exc:
            pel.verify_failed(
                command_id=command_id,
                client_id=str(customer_id),
                error_code=type(exc).__name__,
                error_detail=str(exc),
                duration_ms=int((time.perf_counter() - _start) * 1000),
            )
            logger.exception("Credits GP verify fallback failed: %s", exc)
            # Re-read status before overwriting — webhook may have completed it
            if self._sync_read_command_status(store, command_id) != "completed":
                self._sync_write_command_status(
                    store, command_id, record, "failed", error=str(exc)
                )
            return

        _dur = int((time.perf_counter() - _start) * 1000)
        if result.get("verified"):
            result["verify_path"] = "revenuecat_api_fallback"
            pel.verify_completed(
                command_id=command_id,
                client_id=str(customer_id),
                verify_path="revenuecat_api_fallback",
                duration_ms=_dur,
            )
            pel.payment_captured(
                command_id=command_id, client_id=str(customer_id)
            )
        else:
            pel.verify_failed(
                command_id=command_id,
                client_id=str(customer_id),
                error_code="not_verified",
                duration_ms=_dur,
                error_detail=result.get("message"),
            )

        # Re-read status before writing — webhook may have won the race
        if self._sync_read_command_status(store, command_id) == "completed":
            logger.info(
                "CREDITS_GP_FALLBACK_WEBHOOK_WON_RACE | command=%s",
                command_id,
            )
            return

        final_status = "completed" if result.get("verified") else "failed"
        final_error = None if result.get("verified") else result.get("message")
        self._sync_write_command_status(
            store, command_id, record, final_status,
            result=result, error=final_error,
        )

    async def _verify_via_revenuecat(
        self, customer_id: str, order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Lock order by ID → check already paid → call RevenueCat → grant credits."""
        with self._session_scope() as session:
            settings = self.settings

            def _op() -> Dict[str, Any]:
                # 1) Find the EXACT order by ID, lock it
                if order_id:
                    the_order = (
                        session.query(Order)
                        .filter(Order.id == order_id)
                        .with_for_update()
                        .first()
                    )
                else:
                    the_order = (
                        session.query(Order)
                        .filter(
                            Order.customer_id == customer_id,
                            Order.status == "pending",
                            Order.provider == Provider.google_play.value,
                        )
                        .order_by(Order.created_at.desc())
                        .with_for_update()
                        .first()
                    )

                if not the_order:
                    return CreditVerifyResult(
                        verified=False,
                        captured=False,
                        message="Order not found.",
                    ).model_dump()

                # 2) Already paid? Check if credits were already granted
                if the_order.status == "paid":
                    existing_ledger = (
                        session.query(CreditLedger)
                        .filter(
                            CreditLedger.source_order_id == the_order.id,
                            CreditLedger.txn_type == "purchase",
                        )
                        .first()
                    )
                    if existing_ledger:
                        bal = (
                            session.query(CreditBalance)
                            .filter(CreditBalance.client_id == int(customer_id))
                            .first()
                        )
                        logger.info(
                            "CREDITS_GP_VERIFY_ALREADY_PAID | order=%s",
                            the_order.id,
                        )
                        return CreditVerifyResult(
                            verified=True,
                            captured=True,
                            message="Already fulfilled",
                            order_id=the_order.id,
                            credits_granted=existing_ledger.credits,
                            new_balance=bal.balance if bal else existing_ledger.balance_after,
                        ).model_dump()

                # 3) Determine what to grant from the order items
                credits_to_grant = 0
                product_sku = None
                is_pass = False
                items = (
                    session.query(OrderItem)
                    .filter(OrderItem.order_id == the_order.id)
                    .all()
                )
                for item in items:
                    if item.item_type == "food_scanner_credits":
                        meta = item.item_metadata or {}
                        product_sku = item.sku
                        is_pass = product_sku in SCAN_PASSES
                        credits_to_grant = meta.get("credits", 0)
                        break

                # Fallback applies to credit packs only — never coerce a pass to 50.
                if not is_pass and credits_to_grant <= 0:
                    product_sku = product_sku or "credit_50"
                    credits_to_grant = CREDIT_PACKS.get(product_sku, 50)

                # 4) Call RevenueCat — filter for credit products only
                pel.provider_call_started(
                    command_id=f"cr_verify_{customer_id}",
                    provider_endpoint="get_subscriber",
                )
                _prov_start = time.perf_counter()
                try:
                    subscriber_data = rc_get_subscriber(
                        app_user_id=customer_id,
                        api_key=settings.revenuecat_api_key,
                    )
                    pel.provider_call_completed(
                        command_id=f"cr_verify_{customer_id}",
                        provider_endpoint="get_subscriber",
                        duration_ms=int(
                            (time.perf_counter() - _prov_start) * 1000
                        ),
                    )
                except Exception as prov_exc:
                    pel.provider_call_failed(
                        command_id=f"cr_verify_{customer_id}",
                        provider_endpoint="get_subscriber",
                        error_code=type(prov_exc).__name__,
                        duration_ms=int(
                            (time.perf_counter() - _prov_start) * 1000
                        ),
                    )
                    raise

                # Extract ONLY credit products from non_subscriptions
                subscriber = subscriber_data.get("subscriber", {})
                non_subs = subscriber.get("non_subscriptions", {})
                purchase_data = None
                for pid, purchases in non_subs.items():
                    if any(sku in pid for sku in ALL_CREDIT_SKUS) and purchases:
                        purchase_data = purchases[-1]
                        purchase_data["product_identifier"] = pid
                        break

                if not purchase_data:
                    return CreditVerifyResult(
                        verified=False,
                        captured=False,
                        message="No credit purchase found in RevenueCat.",
                        order_id=the_order.id,
                    ).model_dump()

                store_transaction_id = (
                    purchase_data.get("store_transaction_id")
                    or purchase_data.get("transaction_id")
                    or f"rc_{customer_id}_{int(now_ist().timestamp())}"
                )

                # 5) Mark order paid
                the_order.status = "paid"
                the_order.provider_order_id = store_transaction_id
                session.add(the_order)

                # 6) Create Payment (idempotent on provider_payment_id)
                existing_payment = (
                    session.query(Payment)
                    .filter(
                        Payment.provider == Provider.google_play.value,
                        Payment.provider_payment_id == store_transaction_id,
                    )
                    .first()
                )

                sync_service = SubscriptionSyncService(session)
                if existing_payment:
                    payment_id = existing_payment.id
                    logger.info(
                        "CREDITS_GP_PAYMENT_EXISTS | txn=%s payment=%s",
                        store_transaction_id[:12],
                        payment_id,
                    )
                else:
                    payment_id = sync_service.generate_id("pay")
                    payment = Payment(
                        id=payment_id,
                        order_id=the_order.id,
                        customer_id=customer_id,
                        provider=Provider.google_play.value,
                        provider_payment_id=store_transaction_id,
                        amount_minor=the_order.gross_amount_minor,
                        currency="INR",
                        status="captured",
                        captured_at=now_ist(),
                        payment_metadata={
                            "source": "verify_fallback",
                            "flow": "food_scanner_credits",
                            "verified_at": now_ist().isoformat(),
                        },
                    )
                    session.add(payment)

                # 7) Grant credits OR activate scan pass (both idempotent)
                credit_svc = CreditService(session)
                if is_pass:
                    try:
                        credit_svc.grant_scan_pass(
                            int(customer_id),
                            validity=SCAN_PASSES[product_sku],
                            source_order_id=the_order.id,
                            description=f"Unlimited scan pass ({product_sku})",
                        )
                    except DuplicateGrantError:
                        pass
                    new_balance = credit_svc._lock_or_create_balance(
                        int(customer_id)
                    ).balance
                    credits_to_grant = 0
                else:
                    try:
                        new_balance = credit_svc.grant_credits(
                            client_id=int(customer_id),
                            credits=credits_to_grant,
                            txn_type="purchase",
                            source_order_id=the_order.id,
                            description=f"Purchased {product_sku} ({credits_to_grant} credits)",
                        )
                    except DuplicateGrantError:
                        bal = credit_svc._lock_or_create_balance(int(customer_id))
                        new_balance = bal.balance

                    # 7b) Grant 1 ai_scanner reward entry (packs only, idempotent)
                    try:
                        add_ai_scanner_entry_sync(
                            session=session,
                            client_id=int(customer_id),
                            source_id=the_order.id,
                        )
                    except Exception as reward_exc:
                        logger.warning(
                            "CREDITS_GP_REWARD_ENTRY_ERROR | order=%s sku=%s error=%s",
                            the_order.id, product_sku, reward_exc,
                        )

                session.commit()

                return CreditVerifyResult(
                    verified=True,
                    captured=True,
                    message="Credits purchased successfully",
                    order_id=the_order.id,
                    payment_id=payment_id,
                    credits_granted=credits_to_grant,
                    new_balance=new_balance,
                ).model_dump()

            return await run_sync_db_operation(_op)

    # ================================================================
    # WEBHOOK — grant credits + complete any pending verify command
    # ================================================================

    async def process_webhook(self, command_id: str, store: CommandStore) -> None:
        record = await store.mark_processing(command_id)
        payload = CreditWebhookCommand(**record.payload)
        _start = time.perf_counter()
        pel.webhook_received(command_id=command_id)
        try:
            result = await self._handle_webhook(payload.signature, payload.raw_body)
        except Exception as exc:
            pel.webhook_failed(
                command_id=command_id,
                error_code=type(exc).__name__,
                duration_ms=int((time.perf_counter() - _start) * 1000),
                error_detail=str(exc),
            )
            logger.exception("Credits GP webhook command failed: %s", exc)
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
        self, signature: str, raw_body: str
    ) -> Dict[str, Any]:
        if signature != self.settings.revenuecat_webhook_secret:
            pel.webhook_signature_invalid(command_id="credits_gp_webhook")
            raise ValueError("invalid_webhook_signature")

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid_json: {exc}") from exc

        event = payload.get("event", {})
        customer_id = event.get("app_user_id")
        event_type = event.get("type", "UNKNOWN")
        product_id = event.get("product_id", "")

        is_credit_product = any(sku in product_id for sku in ALL_CREDIT_SKUS)
        if not is_credit_product:
            return CreditWebhookResult(
                status="skipped",
                event_type=event_type,
                reason="not_a_credit_product",
            ).model_dump()

        if event_type in ("INITIAL_PURCHASE", "NON_RENEWING_PURCHASE"):
            return await self._webhook_grant_credits(event, customer_id, product_id)
        elif event_type == "REFUND":
            return await self._webhook_refund_credits(event, customer_id, product_id)
        else:
            return CreditWebhookResult(
                status="ignored", event_type=event_type,
            ).model_dump()

    async def _webhook_grant_credits(
        self, event: dict, customer_id: str, product_id: str
    ) -> Dict[str, Any]:
        sku = _match_sku(product_id)
        is_pass = sku in SCAN_PASSES
        credits_to_grant = 0 if is_pass else (CREDIT_PACKS.get(sku, 50) if sku else 50)

        store_transaction_id = (
            event.get("store_transaction_id")
            or event.get("transaction_id")
            or f"rc_{customer_id}_{int(now_ist().timestamp())}"
        )

        with self._session_scope() as session:

            def _op() -> Dict[str, Any]:
                sync_service = SubscriptionSyncService(session)

                # 1) Find exact order by provider_order_id, or latest credit order
                the_order = (
                    session.query(Order)
                    .filter(
                        Order.customer_id == customer_id,
                        Order.provider_order_id == store_transaction_id,
                    )
                    .with_for_update()
                    .first()
                )

                if not the_order:
                    the_order = (
                        session.query(Order)
                        .join(OrderItem, OrderItem.order_id == Order.id)
                        .filter(
                            Order.customer_id == customer_id,
                            Order.provider == Provider.google_play.value,
                            OrderItem.item_type == "food_scanner_credits",
                        )
                        .order_by(Order.created_at.desc())
                        .with_for_update()
                        .first()
                    )

                if the_order and the_order.status == "pending":
                    the_order.status = "paid"
                    the_order.provider_order_id = store_transaction_id
                    session.add(the_order)

                # 2) Payment idempotency check
                existing_payment = (
                    session.query(Payment)
                    .filter(
                        Payment.provider == Provider.google_play.value,
                        Payment.provider_payment_id == store_transaction_id,
                    )
                    .first()
                )

                if existing_payment:
                    payment_id = existing_payment.id
                    logger.info(
                        "CREDITS_GP_WEBHOOK_PAYMENT_EXISTS | txn=%s payment=%s",
                        store_transaction_id[:12],
                        payment_id,
                    )
                else:
                    payment_id = sync_service.generate_id("pay")
                    payment = Payment(
                        id=payment_id,
                        order_id=the_order.id if the_order else None,
                        customer_id=customer_id,
                        provider=Provider.google_play.value,
                        provider_payment_id=store_transaction_id,
                        amount_minor=the_order.gross_amount_minor
                        if the_order
                        else 0,
                        currency="INR",
                        status="captured",
                        captured_at=now_ist(),
                        payment_metadata={
                            "source": "webhook",
                            "flow": "food_scanner_credits",
                            "event_type": event.get("type"),
                        },
                    )
                    session.add(payment)

                # 3) Grant credits OR activate scan pass (both idempotent)
                credit_svc = CreditService(session)
                order_id_for_grant = (
                    the_order.id if the_order else store_transaction_id
                )
                if is_pass:
                    try:
                        credit_svc.grant_scan_pass(
                            int(customer_id),
                            validity=SCAN_PASSES[sku],
                            source_order_id=order_id_for_grant,
                            description=f"Webhook: unlimited scan pass ({sku})",
                        )
                    except DuplicateGrantError:
                        pass
                    new_balance = credit_svc._lock_or_create_balance(
                        int(customer_id)
                    ).balance
                else:
                    try:
                        new_balance = credit_svc.grant_credits(
                            client_id=int(customer_id),
                            credits=credits_to_grant,
                            txn_type="purchase",
                            source_order_id=order_id_for_grant,
                            description=f"Webhook: {sku or product_id} ({credits_to_grant} credits)",
                        )
                    except DuplicateGrantError:
                        bal = credit_svc._lock_or_create_balance(int(customer_id))
                        new_balance = bal.balance

                    # 3b) Grant 1 ai_scanner reward entry (packs only, idempotent)
                    try:
                        add_ai_scanner_entry_sync(
                            session=session,
                            client_id=int(customer_id),
                            source_id=order_id_for_grant,
                        )
                    except Exception as reward_exc:
                        logger.warning(
                            "CREDITS_GP_WEBHOOK_REWARD_ENTRY_ERROR | order=%s sku=%s error=%s",
                            order_id_for_grant, sku or product_id, reward_exc,
                        )

                session.commit()
                return CreditWebhookResult(
                    status="processed",
                    event_type=event.get("type", "UNKNOWN"),
                    credits_granted=credits_to_grant,
                    new_balance=new_balance,
                    order_id=the_order.id if the_order else None,
                    payment_id=payment_id,
                ).model_dump()

            result = await run_sync_db_operation(_op)

        # ── Post-DB: set markers + complete pending verify ──────────
        if self.redis and customer_id:
            order_id = result.get("order_id") or "unknown"

            # 1. Set capture marker (for any future verify)
            capture_key = _CAPTURE_KEY.format(
                customer_id=customer_id, order_id=order_id
            )
            self.redis.set(
                capture_key,
                json.dumps({
                    "event_type": event.get("type"),
                    "credits": credits_to_grant,
                    "granted_at": now_ist().isoformat(),
                }),
                ex=self.config.credits_capture_cache_ttl_seconds,
            )

            # 2. Check for pending verify command and complete it
            pending_key = _PENDING_VERIFY_KEY.format(customer_id=customer_id)
            pending_raw = self.redis.get(pending_key)
            if pending_raw:
                try:
                    pending = json.loads(pending_raw)
                    pending_cmd_id = pending.get("command_id")
                    if pending_cmd_id:
                        self._complete_pending_verify(
                            pending_cmd_id, result, customer_id
                        )
                except (json.JSONDecodeError, Exception) as exc:
                    logger.warning(
                        "CREDITS_GP_WEBHOOK_COMPLETE_PENDING_FAILED | err=%s",
                        exc,
                    )
        return result

    def _complete_pending_verify(
        self, command_id: str, webhook_result: Dict[str, Any], customer_id: str
    ) -> None:
        """Webhook completes a pending verify command directly in Redis."""
        if not self.redis:
            return

        key = f"{self.config.credits_redis_prefix}:cmd:{command_id}"

        # Read current state
        raw = self.redis.get(key)
        if not raw:
            return
        data = json.loads(raw)

        # Only complete if still in a pending state
        if data.get("status") not in ("pending_webhook", "processing", "queued"):
            logger.info(
                "CREDITS_GP_WEBHOOK_SKIP_COMPLETE | command=%s status=%s",
                command_id,
                data.get("status"),
            )
            return

        # 1. Set done flag FIRST (so fallback sees it before we write status)
        self.redis.set(
            _VERIFY_DONE_KEY.format(command_id=command_id),
            "1",
            ex=60,
        )

        # 2. Write completed status
        data["status"] = "completed"
        data["result"] = CreditVerifyResult(
            verified=True,
            captured=True,
            message="Credits verified via webhook",
            order_id=webhook_result.get("order_id"),
            credits_granted=webhook_result.get("credits_granted"),
            new_balance=webhook_result.get("new_balance"),
            verify_path="completed_by_webhook",
        ).model_dump()
        data["error"] = None
        data["updated_at"] = int(time.time())
        self.redis.set(key, json.dumps(data), ex=self.config.command_ttl_seconds)

        # 3. Clean up pending key
        self.redis.delete(
            _PENDING_VERIFY_KEY.format(customer_id=customer_id)
        )

        logger.info(
            "CREDITS_GP_WEBHOOK_COMPLETED_PENDING_VERIFY | command=%s customer=%s",
            command_id,
            _mask(customer_id),
        )

    async def _webhook_refund_credits(
        self, event: dict, customer_id: str, product_id: str
    ) -> Dict[str, Any]:
        sku = _match_sku(product_id)
        is_pass = sku in SCAN_PASSES
        credits_to_refund = 0 if is_pass else (CREDIT_PACKS.get(sku, 50) if sku else 50)

        with self._session_scope() as session:

            def _op() -> Dict[str, Any]:
                credit_svc = CreditService(session)

                # Scan pass refund — deactivate the unlimited pass, no credits move.
                if is_pass:
                    credit_svc.revoke_scan_pass(
                        int(customer_id),
                        description=f"Refund: unlimited scan pass ({sku})",
                    )
                    bal = credit_svc.get_balance(int(customer_id))
                    session.commit()
                    return CreditWebhookResult(
                        status="processed",
                        event_type="REFUND",
                        credits_refunded=0,
                        new_balance=bal.balance,
                    ).model_dump()

                bal = credit_svc.get_balance(int(customer_id))
                actual_refund = min(credits_to_refund, bal.balance)
                if actual_refund > 0:
                    credit_svc.deduct_credit(
                        int(customer_id),
                        amount=actual_refund,
                        description=f"Refund: {sku or product_id} (-{actual_refund} credits)",
                    )
                    latest = (
                        session.query(CreditLedger)
                        .filter(CreditLedger.client_id == int(customer_id))
                        .order_by(CreditLedger.created_at.desc())
                        .first()
                    )
                    if latest:
                        latest.txn_type = "refunded"
                        session.add(latest)

                session.commit()
                return CreditWebhookResult(
                    status="processed",
                    event_type="REFUND",
                    credits_refunded=actual_refund,
                    new_balance=bal.balance - actual_refund,
                ).model_dump()

            return await run_sync_db_operation(_op)

    # ── Sync Redis helpers (for Celery worker context) ──────────────

    def _sync_read_credit_state(
        self, customer_id: str, *, order_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        with self._session_scope() as session:
            query = session.query(CreditLedger).filter(
                CreditLedger.client_id == int(customer_id),
                CreditLedger.txn_type == "purchase",
            )
            if order_id:
                query = query.filter(CreditLedger.source_order_id == order_id)
            ledger = query.order_by(CreditLedger.created_at.desc()).first()
            if not ledger:
                return None

            bal = (
                session.query(CreditBalance)
                .filter(CreditBalance.client_id == int(customer_id))
                .first()
            )
            return CreditVerifyResult(
                verified=True,
                captured=True,
                message="Credits verified via webhook",
                order_id=ledger.source_order_id,
                credits_granted=ledger.credits,
                new_balance=bal.balance if bal else ledger.balance_after,
            ).model_dump()

    def _sync_read_command_status(
        self, store: CommandStore, command_id: str
    ) -> Optional[str]:
        if not self.redis:
            return None
        raw = self.redis.get(store._key(command_id))
        if not raw:
            return None
        try:
            return json.loads(raw).get("status")
        except (json.JSONDecodeError, TypeError):
            return None

    def _sync_write_command_status(
        self,
        store: CommandStore,
        command_id: str,
        record,
        status: str,
        *,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        if not self.redis:
            return
        key = store._key(command_id)
        raw = self.redis.get(key)
        if raw:
            data = json.loads(raw)
        else:
            data = {
                "command_id": command_id,
                "command_type": "credits_verify",
                "payload": record.payload if record else {},
                "owner_id": record.owner_id if record else None,
                "created_at": record.created_at if record else int(time.time()),
            }
        data["status"] = status
        data["result"] = result
        data["error"] = error
        data["updated_at"] = int(time.time())
        self.redis.set(key, json.dumps(data), ex=self.config.command_ttl_seconds)

    @contextmanager
    def _session_scope(self):
        with self.payment_db.get_session() as session:
            yield session
