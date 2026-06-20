
import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from redis import Redis
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.celery_app import celery_app
from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits._deps.config import (
    HighConcurrencyConfig,
    get_payment_settings,
)
from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits._deps.command_store import (
    CommandStore,
)
from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits._deps.database import (
    PaymentDatabase,
    run_sync_db_operation,
)
from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits._deps.models import (
    Entitlement,
    EntType,
    ItemType,
    Order,
    OrderItem,
    Payment,
    Provider,
    StatusEnt,
)
from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits._deps.sync_service import (
    SubscriptionSyncService,
)
from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits._deps.revenuecat import (
    get_subscriber as rc_get_subscriber,
)
from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits._deps.event_logger import (
    PaymentEventLogger,
)
from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits._deps.utils import (
    now_ist,
)
from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits.credits.shared.credit_service import (
    CreditService,
    DuplicateGrantError,
)
from app.fittbot_api.v1.client.client_api.reward_program.reward_service import (
    add_ai_diet_coach_entry_sync,
    add_nutrition_purchase_entry_sync,
)
from app.models.nutrition_models import AiDietBooking, NutritionEligibility
from app.models.fittbot_payments_models import Payment as FittbotPayment

from ..plans import NutritionPlan, PlanKind, get_plan, get_plan_or_none
from .schemas import (
    NutritionPackagePurchaseCommand,
    NutritionPackagePurchaseResult,
    NutritionPackageVerifyCommand,
    NutritionPackageVerifyResult,
    NutritionPackageWebhookCommand,
    NutritionPackageWebhookResult,
)

logger = logging.getLogger("payments.nutrition_package.v2.googleplay.processor")
pel = PaymentEventLogger("revenuecat", "nutrition_package_purchase")

# ── Redis key templates ───────────────────────────────────────────────
_CAPTURE_KEY = "nutrition_pkg:gp:capture:{customer_id}:{order_id}"
_VERIFY_DONE_KEY = "nutrition_pkg:gp:verify_done:{command_id}"
_PENDING_VERIFY_KEY = "nutrition_pkg:gp:pending_verify:{customer_id}"


def _mask(value: str, left: int = 4, right: int = 4) -> str:
    if not value or len(value) <= left + right:
        return "***"
    return value[:left] + "***" + value[-right:]


class GooglePlayNutritionPackageProcessor:
    """Background worker for Google Play nutrition-package lifecycle."""

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

    # ================================================================
    # PURCHASE — create pending Order (NO slot validation)
    # ================================================================

    async def process_purchase(self, command_id: str, store: CommandStore) -> None:
        record = await store.mark_processing(command_id)
        payload = NutritionPackagePurchaseCommand(**record.payload)
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
        self, payload: NutritionPackagePurchaseCommand
    ) -> Dict[str, Any]:
        plan = get_plan(payload.product_sku)

        with self._session_scope() as session:

            def _op() -> Dict[str, Any]:
                ist_now = now_ist()
                order_id = (
                    f"ord_{ist_now.strftime('%Y%m%d')}"
                    f"_{payload.client_id}"
                    f"_{int(ist_now.timestamp())}"
                )

                order_metadata = {
                    "order_info": {
                        "order_type": "nutrition_package_purchase",
                        "customer_id": payload.client_id,
                        "created_at": ist_now.isoformat(),
                        "currency": payload.currency,
                        "flow": "nutrition_package_googleplay",
                        "plan_sku": plan.sku,
                    },
                    "package": {
                        "plan_name": plan.plan_name,
                        "total_sessions": plan.total_sessions,
                        "session_schedule": plan.session_schedule,
                        "validity_days": plan.validity_days,
                        "bonus_credits": plan.bonus_credits,
                    },
                    "payment_summary": {
                        "final_amount_minor": plan.price_minor,
                        "final_amount_rupees": plan.price_minor / 100,
                    },
                }

                order = Order(
                    id=order_id,
                    customer_id=payload.client_id,
                    currency=payload.currency,
                    provider=Provider.google_play.value,
                    gross_amount_minor=plan.price_minor,
                    status="pending",
                    order_metadata=order_metadata,
                )
                session.add(order)

                sync_service = SubscriptionSyncService(session)
                item_id = sync_service.generate_id("itm")
                item_metadata = {
                    "service_type": "nutrition_package_consultation",
                    "total_sessions": plan.total_sessions,
                    "session_schedule": plan.session_schedule,
                    "amount": plan.price_minor / 100,
                }

                order_item = OrderItem(
                    id=item_id,
                    order_id=order_id,
                    item_type=ItemType.fymble_purchase,
                    sku=payload.product_sku,
                    title=plan.plan_name,
                    unit_price_minor=plan.price_minor,
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

                return NutritionPackagePurchaseResult(
                    order_id=order.id,
                    client_id=payload.client_id,
                    product_sku=payload.product_sku,
                    amount=plan.price_minor,
                    currency=payload.currency,
                    status="pending",
                    api_key=api_key,
                    expires_at=(now_ist() + timedelta(minutes=15)).isoformat(),
                    created_at=order.created_at.isoformat(),
                    total_sessions=plan.total_sessions,
                ).model_dump()

            return await run_sync_db_operation(_op)

    # ================================================================
    # VERIFY — Phase 1: instant Redis check → pending_webhook + fallback
    # ================================================================

    async def process_verify(self, command_id: str, store: CommandStore) -> None:
        record = await store.mark_processing(command_id)
        payload = NutritionPackageVerifyCommand(**record.payload)
        customer_id = payload.client_id
        order_id = payload.order_id

        pel.verify_started(command_id=command_id, client_id=str(customer_id))

        capture_key = _CAPTURE_KEY.format(customer_id=customer_id, order_id=order_id)
        capture_raw = self.redis.get(capture_key) if self.redis else None
        capture_marker = json.loads(capture_raw) if capture_raw else None

        if capture_marker:
            result = self._sync_read_fulfillment_state(customer_id, order_id=order_id)
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

        if self.redis:
            pending_key = _PENDING_VERIFY_KEY.format(customer_id=customer_id)
            self.redis.set(
                pending_key,
                json.dumps({"command_id": command_id, "customer_id": customer_id}),
                ex=30,
            )

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

        celery_app.send_task(
            self.config.gp_nutrition_verify_fallback_queue_name,
            args=[command_id],
            queue="payments",
            countdown=self.config.gp_nutrition_verify_total_timeout_seconds,
        )



    # ================================================================
    # VERIFY FALLBACK — fires after 20s if webhook didn't complete it
    # ================================================================

    async def process_verify_fallback(
        self, command_id: str, store: CommandStore
    ) -> None:
        if self.redis:
            done_key = _VERIFY_DONE_KEY.format(command_id=command_id)
            done_flag = self.redis.get(done_key)
            if done_flag:
                logger.info(
                    "NUTRITION_PKG_GP_FALLBACK_SKIPPED | command=%s (done flag set)",
                    command_id,
                )
                return

        current_status = self._sync_read_command_status(store, command_id)
        if current_status == "completed":
            return
        if current_status not in ("pending_webhook", "processing", "queued"):
            return

        record = await store.get(command_id)
        if not record:
            return

        customer_id = record.payload.get("client_id")
        if not customer_id:
            self._sync_write_command_status(
                store, command_id, record, "failed", error="missing_client_id"
            )
            return

        _start = time.perf_counter()
        pel.verify_started(command_id=command_id, client_id=str(customer_id))
        order_id = record.payload.get("order_id")

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
            pel.payment_captured(command_id=command_id, client_id=str(customer_id))
        else:
            pel.verify_failed(
                command_id=command_id,
                client_id=str(customer_id),
                error_code="not_verified",
                duration_ms=_dur,
                error_detail=result.get("message"),
            )

        if self._sync_read_command_status(store, command_id) == "completed":
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
        with self._session_scope() as session:
            settings = self.settings

            def _op() -> Dict[str, Any]:
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
                    return NutritionPackageVerifyResult(
                        verified=False, captured=False, message="Order not found.",
                    ).model_dump()

                existing_items = session.query(OrderItem).filter(
                    OrderItem.order_id == the_order.id
                ).all()
                plan = (
                    get_plan(existing_items[0].sku)
                    if existing_items else None
                )
                for item in existing_items:
                    existing_ent = session.query(Entitlement).filter(
                        Entitlement.order_item_id == item.id
                    ).first()
                    if existing_ent:
                        return NutritionPackageVerifyResult(
                            verified=True, captured=True,
                            message="Already fulfilled",
                            order_id=the_order.id,
                            entitlement_id=existing_ent.id,
                            total_sessions=plan.total_sessions if plan else None,
                        ).model_dump()

                pel.provider_call_started(
                    command_id=f"nutr_pkg_verify_{customer_id}",
                    provider_endpoint="get_subscriber",
                )
                _prov_start = time.perf_counter()
                try:
                    subscriber_data = rc_get_subscriber(
                        app_user_id=customer_id,
                        api_key=settings.revenuecat_api_key,
                    )
                    pel.provider_call_completed(
                        command_id=f"nutr_pkg_verify_{customer_id}",
                        provider_endpoint="get_subscriber",
                        duration_ms=int((time.perf_counter() - _prov_start) * 1000),
                    )
                except Exception as prov_exc:
                    pel.provider_call_failed(
                        command_id=f"nutr_pkg_verify_{customer_id}",
                        provider_endpoint="get_subscriber",
                        error_code=type(prov_exc).__name__,
                        duration_ms=int((time.perf_counter() - _prov_start) * 1000),
                    )
                    raise

                subscriber = subscriber_data.get("subscriber", {})
                non_subs = subscriber.get("non_subscriptions", {})
                # Prefer the purchase that matches THIS order's SKU; fall back
                # to any catalog SKU only if nothing matches (defensive).
                target_sku = plan.sku if plan else None
                purchase_data = None
                if target_sku and target_sku in non_subs and non_subs[target_sku]:
                    purchase_data = non_subs[target_sku][-1]
                    purchase_data["product_identifier"] = target_sku
                else:
                    for product_id, purchases in non_subs.items():
                        if get_plan_or_none(product_id) is not None and purchases:
                            purchase_data = purchases[-1]
                            purchase_data["product_identifier"] = product_id
                            break

                if not purchase_data:
                    return NutritionPackageVerifyResult(
                        verified=False, captured=False,
                        message="No nutrition purchase found in RevenueCat.",
                        order_id=the_order.id,
                    ).model_dump()

                store_transaction_id = (
                    purchase_data.get("store_transaction_id")
                    or purchase_data.get("transaction_id")
                    or f"rc_{customer_id}_{int(now_ist().timestamp())}"
                )

                if plan is None:
                    return NutritionPackageVerifyResult(
                        verified=False, captured=False,
                        message="Order has no item — cannot resolve plan.",
                        order_id=the_order.id,
                    ).model_dump()

                return self._fulfill_order(
                    session, the_order, existing_items, plan,
                    store_transaction_id, source="verify_fallback",
                )

            return await run_sync_db_operation(_op)

    # ================================================================
    # WEBHOOK — grant + complete any pending verify command
    # ================================================================

    async def process_webhook(self, command_id: str, store: CommandStore) -> None:
        _t0 = time.perf_counter()
     
        record = await store.mark_processing(command_id)
        payload = NutritionPackageWebhookCommand(**record.payload)
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
            expected = self.settings.revenuecat_webhook_secret
            logger.error(
                "WH_SIG_MISMATCH | got_len=%d expected_len=%d "
                "got_first6=%r got_last6=%r exp_first6=%r exp_last6=%r "
                "got_repr=%r",
                len(signature), len(expected),
                signature[:6], signature[-6:],
                expected[:6], expected[-6:],
                signature,
            )
            raise ValueError("invalid_webhook_signature")

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid_json: {exc}") from exc

        event = payload.get("event", {})
        customer_id = event.get("app_user_id")
        event_type = event.get("type", "UNKNOWN")
        product_id = event.get("product_id", "")

        # Catalog membership filter — covers every plan including ai_diet_coach
        # without coupling to a string prefix.
        if get_plan_or_none(product_id) is None:
            return NutritionPackageWebhookResult(
                status="skipped", event_type=event_type,
                reason="not_a_catalog_product",
            ).model_dump()

        if event_type in ("INITIAL_PURCHASE", "NON_RENEWING_PURCHASE"):
            return await self._webhook_grant(event, customer_id)
        else:
            return NutritionPackageWebhookResult(
                status="ignored", event_type=event_type,
            ).model_dump()

    async def _webhook_grant(
        self, event: dict, customer_id: str
    ) -> Dict[str, Any]:
        store_transaction_id = (
            event.get("store_transaction_id")
            or event.get("transaction_id")
            or f"rc_{customer_id}_{int(now_ist().timestamp())}"
        )
        webhook_product_id = event.get("product_id", "")

        with self._session_scope() as session:

            def _op() -> Dict[str, Any]:
                # Path 1: idempotent re-delivery — order already linked to this
                # store_transaction_id (set during a prior fulfillment).
                the_order = (
                    session.query(Order)
                    .filter(
                        Order.customer_id == customer_id,
                        Order.provider_order_id == store_transaction_id,
                    )
                    .with_for_update()
                    .first()
                )

                # Path 2: SKU-scoped fallback — find the most recent pending
                # order for THIS specific plan SKU. This prevents cross-plan
                # confusion when the customer has concurrent pending orders
                # for different plans (e.g. 4-session and ai_diet_coach).
                if not the_order and webhook_product_id:
                    the_order = (
                        session.query(Order)
                        .join(OrderItem, OrderItem.order_id == Order.id)
                        .filter(
                            Order.customer_id == customer_id,
                            Order.provider == Provider.google_play.value,
                            Order.status == "pending",
                            OrderItem.item_type == ItemType.fymble_purchase,
                            OrderItem.sku == webhook_product_id,
                        )
                        .order_by(Order.created_at.desc())
                        .with_for_update()
                        .first()
                    )

                if not the_order:
                    return NutritionPackageWebhookResult(
                        status="skipped",
                        event_type=event.get("type", "UNKNOWN"),
                        reason="no_nutrition_order_found",
                    ).model_dump()

                items = session.query(OrderItem).filter(
                    OrderItem.order_id == the_order.id
                ).all()
                for item in items:
                    existing_ent = session.query(Entitlement).filter(
                        Entitlement.order_item_id == item.id
                    ).first()
                    if existing_ent:
                        return NutritionPackageWebhookResult(
                            status="already_processed",
                            event_type=event.get("type", "UNKNOWN"),
                            order_id=the_order.id,
                            entitlement_id=existing_ent.id,
                        ).model_dump()

                if not items:
                    return NutritionPackageWebhookResult(
                        status="skipped",
                        event_type=event.get("type", "UNKNOWN"),
                        reason="order_has_no_items",
                    ).model_dump()
                plan = get_plan(items[0].sku)

                result = self._fulfill_order(
                    session, the_order, items, plan,
                    store_transaction_id, source="webhook",
                    event_type=event.get("type"),
                )

                return NutritionPackageWebhookResult(
                    status="processed",
                    event_type=event.get("type", "UNKNOWN"),
                    order_id=result.get("order_id"),
                    payment_id=result.get("payment_id"),
                    entitlement_id=result.get("entitlement_id"),
                    eligibility_id=result.get("eligibility_id"),
                    total_sessions=result.get("total_sessions"),
                    credits_granted=result.get("credits_granted"),
                    credits_balance=result.get("credits_balance"),
                ).model_dump()

            _db_t0 = time.perf_counter()
            result = await run_sync_db_operation(_op)


        # Post-DB: set markers + complete pending verify
        if self.redis and customer_id:
            order_id = result.get("order_id") or "unknown"

            _r_t0 = time.perf_counter()
            capture_key = _CAPTURE_KEY.format(
                customer_id=customer_id, order_id=order_id
            )
            self.redis.set(
                capture_key,
                json.dumps({
                    "event_type": event.get("type"),
                    "granted_at": now_ist().isoformat(),
                }),
                ex=self.config.gp_nutrition_capture_cache_ttl_seconds,
            )

            pending_key = _PENDING_VERIFY_KEY.format(customer_id=customer_id)
            pending_raw = self.redis.get(pending_key)
            completed_pending = False
            if pending_raw:
                try:
                    pending = json.loads(pending_raw)
                    pending_cmd_id = pending.get("command_id")
                    if pending_cmd_id:
                        self._complete_pending_verify(
                            pending_cmd_id, result, customer_id
                        )
                        completed_pending = True
                except (json.JSONDecodeError, Exception) as exc:
                    logger.warning(
                        "NUTRITION_PKG_GP_WEBHOOK_COMPLETE_PENDING_FAILED | err=%s", exc
                    )

        return result

    # ================================================================
    # CORE FULFILLMENT — NO booking, just payment + eligibility
    # ================================================================

    def _fulfill_order(
        self,
        session: Session,
        order: Order,
        items: list,
        plan: NutritionPlan,
        store_transaction_id: str,
        *,
        source: str = "verify",
        event_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Atomic fulfillment under the caller's FOR UPDATE lock on Order.
        Writes: Payment → Entitlement → NutritionEligibility → FittbotPayment →
        plan.bonus_credits AI credits (skipped if 0) → plan.reward_entries_count
        reward entries (skipped if 0).
        NO NutritionBooking — client books separately via Fymble module.
        """
        sync_service = SubscriptionSyncService(session)
        customer_id = order.customer_id

        # ── 1. Mark order paid ────────────────────────────────────────
        order.status = "paid"
        order.provider_order_id = store_transaction_id
        session.add(order)

        # ── 2. Create Payment (idempotent on provider_payment_id) ─────
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
        else:
            payment_id = sync_service.generate_id("pay")
            payment = Payment(
                id=payment_id,
                order_id=order.id,
                customer_id=customer_id,
                provider=Provider.google_play.value,
                provider_payment_id=store_transaction_id,
                amount_minor=order.gross_amount_minor,
                currency="INR",
                status="captured",
                captured_at=now_ist(),
                payment_metadata={
                    "source": source,
                    "flow": plan.flow,
                    "event_type": event_type,
                    "verified_at": now_ist().isoformat(),
                },
            )
            session.add(payment)
            try:
                session.flush()
            except IntegrityError:
                session.rollback()
                order = (
                    session.query(Order)
                    .filter(Order.id == order.id)
                    .with_for_update()
                    .first()
                )
                if order and order.status != "paid":
                    order.status = "paid"
                    order.provider_order_id = store_transaction_id
                    session.add(order)
                existing_pay = (
                    session.query(Payment)
                    .filter(
                        Payment.provider == Provider.google_play.value,
                        Payment.provider_payment_id == store_transaction_id,
                    )
                    .first()
                )
                payment_id = existing_pay.id if existing_pay else "unknown"

        # ── 3. Create Entitlement (EntType per plan.kind) ─────────────
        entitlement_id = sync_service.generate_id("ent")
        item = items[0] if items else None
        ent_type = (
            EntType.nutrition
            if plan.kind == PlanKind.session_package
            else EntType.ai_diet_coach
        )
        entitlement = Entitlement(
            id=entitlement_id,
            order_item_id=item.id if item else None,
            customer_id=customer_id,
            gym_id=None,
            entitlement_type=ent_type,
            active_from=now_ist(),
            active_until=now_ist() + timedelta(days=plan.validity_days),
            status=StatusEnt.active,
        )
        session.add(entitlement)
        session.flush()

        # ── 4. Per-kind grant record ─────────────────────────────────
        eligibility = None
        ai_diet_booking = None
        if plan.kind == PlanKind.session_package:
            eligibility = NutritionEligibility(
                client_id=int(customer_id),
                gym_id=None,
                source_type="fymble_purchase",
                source_id=order.id,
                plan_name=plan.plan_name,
                plan_duration_months=0,
                total_sessions=plan.total_sessions,
                used_sessions=0,
                remaining_sessions=plan.total_sessions,
                session_schedule=plan.session_schedule,
                last_booking_date=None,
                granted_at=datetime.now(),
                expires_at=datetime.now() + timedelta(days=plan.validity_days),
            )
            session.add(eligibility)
            session.flush()
        elif plan.kind == PlanKind.ai_diet_coach:
            ai_diet_booking = AiDietBooking(
                client_id=int(customer_id),
                gym_id=None,
                source_type="fymble_purchase",
                source_id=order.id,
                entitlement_id=entitlement_id,
                plan_name=plan.plan_name,
                granted_at=datetime.now(),
                expires_at=datetime.now() + timedelta(days=plan.validity_days),
                status="active",
                plans_generated=0,
                last_generated_at=None,
            )
            session.add(ai_diet_booking)
            session.flush()

        # ── 5. Create FittbotPayment ──────────────────────────────────
        fittbot_payment = FittbotPayment(
            gym_id=0,
            client_id=int(customer_id),
            entitlement_id=order.id,
            source_type="fymble_purchase",
            amount_gross=order.gross_amount_minor / 100,
            amount_net=0,
            currency="INR",
            gateway="google_play",
            gateway_payment_id=store_transaction_id,
            payment_method="google_play",
            is_no_cost_emi=False,
            status="paid",
            paid_at=now_ist(),
        )
        session.add(fittbot_payment)

        # ── 6. Grant AI credits (skipped if plan.bonus_credits == 0) ─
        credit_svc = CreditService(session)
        credits_granted = plan.bonus_credits
        if credits_granted > 0:
            try:
                new_balance = credit_svc.grant_credits(
                    client_id=int(customer_id),
                    credits=credits_granted,
                    txn_type="purchase_bonus",
                    source_order_id=order.id,
                    description=f"Nutrition package bonus ({credits_granted} credits)",
                )
            except DuplicateGrantError:
                bal = credit_svc._lock_or_create_balance(int(customer_id))
                new_balance = bal.balance
        else:
            bal = credit_svc._lock_or_create_balance(int(customer_id))
            new_balance = bal.balance

        # ── 7. Grant reward program entries (per-plan count) ─────────
        if plan.reward_entries_count > 0:
            reward_grant = (
                add_ai_diet_coach_entry_sync
                if plan.kind == PlanKind.ai_diet_coach
                else add_nutrition_purchase_entry_sync
            )
            try:
                reward_ok, entries_added, reward_msg = reward_grant(
                    session=session,
                    client_id=int(customer_id),
                    source_id=order.id,
                    entries_to_add=plan.reward_entries_count,
                )
                if reward_ok:
                    logger.info(
                        "NUTRITION_PKG_REWARD_ENTRIES | order=%s client=%s entries=%d msg=%s",
                        order.id, _mask(customer_id), entries_added, reward_msg,
                    )
                else:
                    logger.warning(
                        "NUTRITION_PKG_REWARD_SKIP | order=%s client=%s msg=%s",
                        order.id, _mask(customer_id), reward_msg,
                    )
            except Exception as reward_exc:
                logger.warning(
                    "NUTRITION_PKG_REWARD_ERROR | order=%s client=%s error=%s",
                    order.id, _mask(customer_id), reward_exc,
                )

        session.commit()

        # ── 8. Invalidate home cache ─────────────────────────────────
        self._invalidate_home_cache(int(customer_id))



        if plan.kind == PlanKind.session_package:
            success_msg = (
                f"{plan.plan_name} purchased successfully — "
                f"{plan.total_sessions} session(s) granted"
            )
        else:
            success_msg = f"{plan.plan_name} activated"

        return NutritionPackageVerifyResult(
            verified=True,
            captured=True,
            message=success_msg,
            order_id=order.id,
            payment_id=payment_id,
            entitlement_id=entitlement_id,
            eligibility_id=eligibility.id if eligibility else None,
            total_sessions=plan.total_sessions,
            credits_granted=credits_granted,
            credits_balance=new_balance,
        ).model_dump()

    # ================================================================
    # PENDING VERIFY COMPLETION (called by webhook)
    # ================================================================

    def _complete_pending_verify(
        self, command_id: str, webhook_result: Dict[str, Any], customer_id: str
    ) -> None:
        if not self.redis:
            return

        key = f"{self.config.gp_nutrition_redis_prefix}:cmd:{command_id}"
        raw = self.redis.get(key)
        if not raw:
            return
        data = json.loads(raw)

        if data.get("status") not in ("pending_webhook", "processing", "queued"):
            return

        self.redis.set(
            _VERIFY_DONE_KEY.format(command_id=command_id), "1", ex=60,
        )

        data["status"] = "completed"
        data["result"] = NutritionPackageVerifyResult(
            verified=True,
            captured=True,
            message="Nutrition package verified via webhook",
            order_id=webhook_result.get("order_id"),
            entitlement_id=webhook_result.get("entitlement_id"),
            eligibility_id=webhook_result.get("eligibility_id"),
            total_sessions=webhook_result.get("total_sessions"),
            credits_granted=webhook_result.get("credits_granted"),
            credits_balance=webhook_result.get("credits_balance"),
            verify_path="completed_by_webhook",
        ).model_dump()
        data["error"] = None
        data["updated_at"] = int(time.time())
        self.redis.set(key, json.dumps(data), ex=self.config.command_ttl_seconds)

        self.redis.delete(_PENDING_VERIFY_KEY.format(customer_id=customer_id))



    # ── Sync helpers ──────────────────────────────────────────────────

    def _sync_read_fulfillment_state(
        self, customer_id: str, *, order_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        with self._session_scope() as session:
            plan = None
            eligibility = None
            if order_id:
                first_item = (
                    session.query(OrderItem)
                    .filter(OrderItem.order_id == order_id)
                    .first()
                )
                if first_item:
                    plan = get_plan_or_none(first_item.sku)

                # Find entitlement linked to THIS order's item — accurate
                # regardless of which plan the customer most-recently bought.
                ent = (
                    session.query(Entitlement)
                    .join(OrderItem, OrderItem.id == Entitlement.order_item_id)
                    .filter(OrderItem.order_id == order_id)
                    .first()
                )

                if plan and plan.kind == PlanKind.session_package:
                    eligibility = (
                        session.query(NutritionEligibility)
                        .filter(
                            NutritionEligibility.client_id == int(customer_id),
                            NutritionEligibility.source_id == order_id,
                        )
                        .first()
                    )
            else:
                ent = (
                    session.query(Entitlement)
                    .filter(Entitlement.customer_id == customer_id)
                    .order_by(Entitlement.created_at.desc())
                    .first()
                )

            if not ent:
                return None

            from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits._deps.models import (
                CreditBalance,
            )
            bal = (
                session.query(CreditBalance)
                .filter(CreditBalance.client_id == int(customer_id))
                .first()
            )

            return NutritionPackageVerifyResult(
                verified=True,
                captured=True,
                message="Verified via webhook",
                order_id=order_id,
                entitlement_id=ent.id,
                eligibility_id=eligibility.id if eligibility else None,
                total_sessions=(
                    eligibility.total_sessions if eligibility
                    else (plan.total_sessions if plan else None)
                ),
                credits_granted=plan.bonus_credits if plan else None,
                credits_balance=bal.balance if bal else None,
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
        self, store: CommandStore, command_id: str, record,
        status: str, *, result: Optional[Dict[str, Any]] = None,
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
                "command_type": "nutrition_package_verify",
                "payload": record.payload if record else {},
                "owner_id": record.owner_id if record else None,
                "created_at": record.created_at if record else int(time.time()),
            }
        data["status"] = status
        data["result"] = result
        data["error"] = error
        data["updated_at"] = int(time.time())
        self.redis.set(key, json.dumps(data), ex=self.config.command_ttl_seconds)

    @staticmethod
    def _invalidate_home_cache(client_id: int) -> None:
        try:
            from app.utils.redis_config import get_redis_sync
            r = get_redis_sync()
            keys = r.keys(f"home:data:{client_id}:*")
            ustate_keys = r.keys(f"home:v2:ustate:{client_id}")
            all_keys = (keys or []) + (ustate_keys or [])
            if all_keys:
                r.delete(*all_keys)
        except Exception:
            pass

    @contextmanager
    def _session_scope(self):
        with self.payment_db.get_session() as session:
            yield session
