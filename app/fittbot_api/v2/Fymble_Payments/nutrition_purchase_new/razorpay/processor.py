

import asyncio
import hashlib
import hmac
import json
import logging
import time
from contextlib import contextmanager
from datetime import date as date_type, datetime, time as time_type, timedelta
from typing import Any, Dict, Optional

from redis import Redis
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

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
    CreditBalance,
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
from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits._deps.event_logger import (
    PaymentEventLogger,
)
from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits._deps.utils import (
    now_ist,
)
from app.fittbot_api.v1.payments.razorpay_async_gateway import (
    create_order as rzp_create_order,
    get_payment as rzp_get_payment,
)
from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits.credits.shared.credit_service import (
    CreditService,
    DuplicateGrantError,
)
from app.fittbot_api.v1.client.client_api.reward_program.reward_service import (
    add_ai_diet_coach_entry_sync,
    add_nutrition_purchase_entry_sync,
)
from app.models.nutrition_models import (
    AiDietBooking,
    NutritionBooking,
    NutritionEligibility,
    NutritionSchedule,
    Nutritionist,
)
from app.models.fittbot_payments_models import Payment as FittbotPayment

from .. import slot_hold
from ..plans import NutritionPlan, PlanKind, get_plan, get_plan_or_none
from .schemas import (
    RpNutritionPackageCheckoutCommand,
    RpNutritionPackageCheckoutResult,
    RpNutritionPackageVerifyCommand,
    RpNutritionPackageVerifyResult,
    RpNutritionPackageWebhookCommand,
    RpNutritionPackageWebhookResult,
)

logger = logging.getLogger("payments.nutrition_package.v2.razorpay.processor")
pel = PaymentEventLogger("razorpay", "nutrition_package_purchase")

NUTRITION_PACKAGE_FLOW = "nutrition_package_razorpay"

# Redis key — namespaced under rp_nutrition_pkg to avoid collisions.
_CAPTURE_KEY = "nutrition_pkg:rp:capture:{payment_id}"


class SlotUnavailableError(ValueError):
    """Raised when the requested slot is taken or held by someone else.

    Subclass of ValueError so the standard checkout error path returns 400,
    while still letting callers detect this specific case.
    """


def _mask(value: str, left: int = 4, right: int = 4) -> str:
    if not value or len(value) <= left + right:
        return "***"
    return value[:left] + "***" + value[-right:]


def _parse_time(s: str) -> time_type:
    """Parse HH:MM or HH:MM:SS string to time object."""
    parts = s.split(":")
    return time_type(
        int(parts[0]),
        int(parts[1]),
        int(parts[2]) if len(parts) > 2 else 0,
    )


def _times_overlap(
    s1: time_type, e1: time_type, s2: time_type, e2: time_type
) -> bool:
    """Two [start,end) time ranges overlap iff s1<e2 and s2<e1."""
    return s1 < e2 and s2 < e1


def _fmt_time(t: time_type) -> str:
    return t.strftime("%H:%M")


def _verify_checkout_signature(
    key_secret: str,
    order_id: str,
    payment_id: str,
    signature: str,
) -> bool:
    """Verify Razorpay checkout signature (HMAC-SHA256 of order_id|payment_id)."""
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
    """Verify Razorpay webhook signature (HMAC-SHA256 of raw body)."""
    try:
        expected = hmac.new(
            webhook_secret.encode("utf-8"),
            raw_body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False


class RazorpayNutritionPackageProcessor:
    """Background worker for Razorpay nutrition-package lifecycle."""

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
    # CHECKOUT — pending Order + OrderItem + rzp_create_order
    # ================================================================

    async def process_checkout(self, command_id: str, store: CommandStore) -> None:
        record = await store.mark_processing(command_id)
        payload = RpNutritionPackageCheckoutCommand(**record.payload)
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
            logger.exception("RP nutrition package checkout failed: %s", exc)
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
        self, payload: RpNutritionPackageCheckoutCommand
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

                # If checkout carries a slot selection (ad-funnel flow),
                # validate the slot against current bookings + holds and
                # acquire a Redis hold BEFORE writing the order. On any
                # failure, raise so the worker marks the command failed
                # without leaving an orphaned order row.
                booking_meta: Optional[Dict[str, Any]] = None
                if payload.booking is not None:
                    booking_meta = self._validate_and_hold_slot_sync(
                        session=session,
                        booking=payload.booking,
                        order_id=order_id,
                    )

                order_metadata = {
                    "order_info": {
                        "order_type": "nutrition_package_purchase",
                        "customer_id": payload.client_id,
                        "created_at": ist_now.isoformat(),
                        "currency": payload.currency,
                        "flow": NUTRITION_PACKAGE_FLOW,
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
                if booking_meta is not None:
                    order_metadata["booking"] = booking_meta

                order = Order(
                    id=order_id,
                    customer_id=payload.client_id,
                    currency=payload.currency,
                    provider=Provider.razorpay_pg.value,
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

                # Fetch client phone for Razorpay prefill
                from app.models.fittbot_models.client import Client
                client_contact = (
                    session.query(Client.contact)
                    .filter(Client.client_id == int(payload.client_id))
                    .scalar()
                )

                return {
                    "order_id": order_id,
                    "amount_minor": plan.price_minor,
                    "currency": payload.currency,
                    "created_at": order.created_at.isoformat(),
                    "prefill": {
                        "email": "support@fymble.app",
                        "contact": client_contact or "",
                    },
                    "booking_meta": booking_meta,
                }

            db_result = await run_sync_db_operation(_op)

        # Create Razorpay order outside the DB transaction
        pel.provider_call_started(
            command_id=f"rp_nutr_pkg_checkout_{payload.client_id}",
            provider_endpoint="create_order",
        )
        _prov_start = time.perf_counter()
        try:
            rzp_order = await rzp_create_order(
                amount_minor=db_result["amount_minor"],
                currency=db_result["currency"],
                receipt=db_result["order_id"],
                notes={
                    "flow": NUTRITION_PACKAGE_FLOW,
                    "customer_id": payload.client_id,
                    "product_sku": payload.product_sku,
                    "total_sessions": str(plan.total_sessions),
                },
            )
            pel.provider_call_completed(
                command_id=f"rp_nutr_pkg_checkout_{payload.client_id}",
                provider_endpoint="create_order",
                duration_ms=int((time.perf_counter() - _prov_start) * 1000),
            )
        except Exception as prov_exc:
            pel.provider_call_failed(
                command_id=f"rp_nutr_pkg_checkout_{payload.client_id}",
                provider_endpoint="create_order",
                error_code=type(prov_exc).__name__,
                duration_ms=int((time.perf_counter() - _prov_start) * 1000),
            )
            # Razorpay order creation failed — release the slot hold so
            # the user can retry without waiting for the 15-min TTL.
            self._release_hold_from_meta(
                db_result.get("booking_meta"), db_result["order_id"]
            )
            raise

        provider_order_id = rzp_order.get("id", "")

        # Update Order with provider_order_id
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

        settings = self.settings
        return RpNutritionPackageCheckoutResult(
            order_id=db_result["order_id"],
            client_id=payload.client_id,
            product_sku=payload.product_sku,
            amount=db_result["amount_minor"],
            currency=db_result["currency"],
            status="pending",
            key_id=settings.razorpay_key_id,
            provider_order_id=provider_order_id,
            prefill=db_result["prefill"],
            expires_at=(now_ist() + timedelta(minutes=15)).isoformat(),
            created_at=db_result["created_at"],
            total_sessions=plan.total_sessions,
        ).model_dump()

    # ================================================================
    # VERIFY — signature + capture-marker fast path + DB grant directly
    # (DailyPass-style: the worker does the full work end-to-end. DB
    #  unique constraints + capture marker arbitrate any race with the
    #  webhook.)
    # ================================================================

    async def process_verify(self, command_id: str, store: CommandStore) -> None:
        record = await store.mark_processing(command_id)
        payload = RpNutritionPackageVerifyCommand(**record.payload)
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
            # 2. Fast path: capture marker (webhook already fulfilled)
            capture_raw = (
                self.redis.get(_CAPTURE_KEY.format(payment_id=razorpay_payment_id))
                if self.redis
                else None
            )
            if capture_raw:
                fast = self._sync_read_fulfillment_state(
                    customer_id, order_id=order_id
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

            # 3. Fast path: existing Payment in DB (marker may have evicted)
            if self._sync_check_existing_payment(razorpay_payment_id):
                fast = self._sync_read_fulfillment_state(
                    customer_id, order_id=order_id
                )
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

            # 4. Webhook-preferred wait: poll the capture marker for up to
            #    20s (12 attempts, 600ms → 4s exp backoff). Mirrors the old
            #    DailyPass _await_capture_marker semantics. If the webhook
            #    lands during this window, we read fulfillment from DB and
            #    skip the outbound Razorpay API call.
            awaited = await self._await_capture_marker(razorpay_payment_id)
            if awaited:
                fast = self._sync_read_fulfillment_state(
                    customer_id, order_id=order_id
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
            logger.exception("RP nutrition package verify failed: %s", exc)
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
            pel.payment_captured(command_id=command_id, client_id=str(customer_id))
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
        """Call Razorpay get_payment, then fulfill in DB."""

        pel.provider_call_started(
            command_id=f"rp_nutr_pkg_verify_{customer_id}",
            provider_endpoint="get_payment",
        )
        _prov_start = time.perf_counter()
        try:
            payment_data = await rzp_get_payment(razorpay_payment_id)
            pel.provider_call_completed(
                command_id=f"rp_nutr_pkg_verify_{customer_id}",
                provider_endpoint="get_payment",
                duration_ms=int((time.perf_counter() - _prov_start) * 1000),
            )
        except Exception as prov_exc:
            pel.provider_call_failed(
                command_id=f"rp_nutr_pkg_verify_{customer_id}",
                provider_endpoint="get_payment",
                error_code=type(prov_exc).__name__,
                duration_ms=int((time.perf_counter() - _prov_start) * 1000),
            )
            raise

        rzp_status = payment_data.get("status", "")
        if rzp_status != "captured":
            return RpNutritionPackageVerifyResult(
                verified=False, captured=False,
                message=f"Payment not captured (status={rzp_status})",
                order_id=order_id,
            ).model_dump()

        with self._session_scope() as session:

            def _op() -> Dict[str, Any]:
                # Lock the exact order
                the_order = None
                if order_id:
                    the_order = (
                        session.query(Order)
                        .filter(Order.id == order_id)
                        .with_for_update()
                        .first()
                    )
                if not the_order:
                    return RpNutritionPackageVerifyResult(
                        verified=False, captured=False,
                        message="Order not found.",
                        order_id=order_id,
                    ).model_dump()

                # Already fulfilled?
                items = session.query(OrderItem).filter(
                    OrderItem.order_id == the_order.id
                ).all()
                plan = get_plan(items[0].sku) if items else None
                for item in items:
                    existing_ent = session.query(Entitlement).filter(
                        Entitlement.order_item_id == item.id
                    ).first()
                    if existing_ent:
                        return RpNutritionPackageVerifyResult(
                            verified=True, captured=True,
                            message="Already fulfilled",
                            order_id=the_order.id,
                            entitlement_id=existing_ent.id,
                            total_sessions=plan.total_sessions if plan else None,
                        ).model_dump()

                if plan is None:
                    return RpNutritionPackageVerifyResult(
                        verified=False, captured=False,
                        message="Order has no item — cannot resolve plan.",
                        order_id=the_order.id,
                    ).model_dump()

                return self._fulfill_order(
                    session, the_order, items, plan,
                    razorpay_payment_id, payment_data,
                    source="verify",
                )

            return await run_sync_db_operation(_op)

    # ================================================================
    # WEBHOOK — payment.captured -> fulfill, payment.failed -> mark failed
    # ================================================================

    async def process_webhook(self, command_id: str, store: CommandStore) -> None:
        record = await store.mark_processing(command_id)
        payload = RpNutritionPackageWebhookCommand(**record.payload)
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
            logger.exception("RP nutrition package webhook failed: %s", exc)
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
        if not _verify_webhook_signature(
            raw_body, razorpay_signature, self.settings.razorpay_webhook_secret
        ):
            pel.webhook_signature_invalid(command_id="rp_nutrition_pkg_webhook")
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

        # Only handle the nutrition-package razorpay flow
        if flow != NUTRITION_PACKAGE_FLOW:
            return RpNutritionPackageWebhookResult(
                status="skipped",
                event_type=event_type,
                reason="not_a_nutrition_package_purchase",
            ).model_dump()

        if event_type == "payment.captured":
            return await self._webhook_grant(
                payment_entity, razorpay_payment_id, razorpay_order_id, notes
            )
        
        elif event_type == "payment.failed":
            return await self._webhook_payment_failed(
                razorpay_order_id, razorpay_payment_id
            )
        
        else:
            return RpNutritionPackageWebhookResult(
                status="ignored",
                event_type=event_type,
            ).model_dump()

    async def _webhook_grant(
        self,
        payment_entity: dict,
        razorpay_payment_id: str,
        razorpay_order_id: str,
        notes: dict,
    ) -> Dict[str, Any]:
        customer_id = notes.get("customer_id", "")

        with self._session_scope() as session:

            def _op() -> Dict[str, Any]:
                # Find the order created by checkout
                the_order = (
                    session.query(Order)
                    .filter(Order.provider_order_id == razorpay_order_id)
                    .with_for_update()
                    .first()
                )

                if not the_order:
                    return RpNutritionPackageWebhookResult(
                        status="skipped",
                        event_type="payment.captured",
                        reason="no_order_found_for_razorpay_order_id",
                    ).model_dump()

                # Already fulfilled? Idempotent.
                items = session.query(OrderItem).filter(
                    OrderItem.order_id == the_order.id
                ).all()
                for item in items:
                    existing_ent = session.query(Entitlement).filter(
                        Entitlement.order_item_id == item.id
                    ).first()
                    if existing_ent:
                        return RpNutritionPackageWebhookResult(
                            status="already_processed",
                            event_type="payment.captured",
                            order_id=the_order.id,
                            entitlement_id=existing_ent.id,
                        ).model_dump()

                if not items:
                    return RpNutritionPackageWebhookResult(
                        status="skipped",
                        event_type="payment.captured",
                        reason="order_has_no_items",
                    ).model_dump()
                plan = get_plan(items[0].sku)

                fulfill_result = self._fulfill_order(
                    session, the_order, items, plan,
                    razorpay_payment_id, payment_entity,
                    source="webhook",
                    event_type="payment.captured",
                )

                return RpNutritionPackageWebhookResult(
                    status="processed",
                    event_type="payment.captured",
                    order_id=fulfill_result.get("order_id"),
                    payment_id=fulfill_result.get("payment_id"),
                    entitlement_id=fulfill_result.get("entitlement_id"),
                    eligibility_id=fulfill_result.get("eligibility_id"),
                    total_sessions=fulfill_result.get("total_sessions"),
                    credits_granted=fulfill_result.get("credits_granted"),
                    credits_balance=fulfill_result.get("credits_balance"),
                ).model_dump()

            result = await run_sync_db_operation(_op)

        # Post-DB: set capture marker so any concurrent verify hits the fast path.
        if self.redis and razorpay_payment_id:
            self.redis.set(
                _CAPTURE_KEY.format(payment_id=razorpay_payment_id),
                json.dumps({
                    "event_type": "payment.captured",
                    "granted_at": now_ist().isoformat(),
                }),
                ex=self.config.rp_nutrition_pkg_capture_cache_ttl_seconds,
            )
        return result

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
                # Release ad-funnel slot hold (if any) so other users can
                # pick this slot immediately instead of waiting for TTL.
                if order and order.order_metadata:
                    booking_meta = order.order_metadata.get("booking")
                    if booking_meta:
                        self._release_hold_from_meta(booking_meta, order.id)
                if order and order.status == "pending":
                    order.status = "failed"
                    session.add(order)
                session.commit()
                return RpNutritionPackageWebhookResult(
                    status="processed",
                    event_type="payment.failed",
                    order_id=order.id if order else None,
                ).model_dump()

            return await run_sync_db_operation(_op)

    # ================================================================
    # CORE FULFILLMENT — mirrors GP _fulfill_order exactly
    # Payment + Entitlement + NutritionEligibility + FittbotPayment +
    # plan.bonus_credits AI credits + plan.reward_entries_count reward entries.
    # NO NutritionBooking.
    # ================================================================

    def _fulfill_order(
        self,
        session: Session,
        order: Order,
        items: list,
        plan: NutritionPlan,
        razorpay_payment_id: str,
        payment_data: Dict[str, Any],
        *,
        source: str = "verify",
        event_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        sync_service = SubscriptionSyncService(session)
        customer_id = order.customer_id

        # ── 1. Mark order paid ────────────────────────────────────────
        order.status = "paid"
        session.add(order)

        # ── 2. Create Payment (idempotent on provider_payment_id) ─────
        existing_payment = (
            session.query(Payment)
            .filter(
                Payment.provider == Provider.razorpay_pg.value,
                Payment.provider_payment_id == razorpay_payment_id,
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
                provider=Provider.razorpay_pg.value,
                provider_payment_id=razorpay_payment_id,
                amount_minor=int(
                    payment_data.get("amount", order.gross_amount_minor)
                ),
                currency=payment_data.get("currency", "INR"),
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
                    session.add(order)
                existing_pay = (
                    session.query(Payment)
                    .filter(
                        Payment.provider == Provider.razorpay_pg.value,
                        Payment.provider_payment_id == razorpay_payment_id,
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

        # ── 4b. Auto-book slot if checkout carried a booking selection ─
        # Ad-funnel flow: the user picked a slot before paying. We held
        # it in Redis at checkout time and stored the slot details in
        # order.order_metadata. Now insert the NutritionBooking inline so
        # the success page shows a confirmed booking, and consume the
        # session immediately.
        auto_booking_id: Optional[int] = None
        auto_booking_status: Optional[str] = None
        auto_booking_meta: Optional[Dict[str, Any]] = None
        if eligibility is not None and order.order_metadata:
            auto_booking_meta = order.order_metadata.get("booking")
        if auto_booking_meta and eligibility is not None:
            try:
                _book_date = date_type.fromisoformat(auto_booking_meta["booking_date"])
                _bs = _parse_time(auto_booking_meta["start_time"])
                _be = _parse_time(auto_booking_meta["end_time"])
                _duration = (
                    (_be.hour * 60 + _be.minute) - (_bs.hour * 60 + _bs.minute)
                ) or 60
                nb = NutritionBooking(
                    client_id=int(customer_id),
                    eligibility_id=eligibility.id,
                    nutritionist_id=int(auto_booking_meta["nutritionist_id"]),
                    schedule_id=int(auto_booking_meta["schedule_id"]),
                    booking_date=_book_date,
                    start_time=_bs,
                    end_time=_be,
                    status="booked",
                    session_number=1,
                    duration_minutes=_duration,
                )
                session.add(nb)
                session.flush()
                eligibility.used_sessions = 1
                eligibility.remaining_sessions = max(
                    0, eligibility.total_sessions - 1
                )
                eligibility.last_booking_date = _book_date
                session.add(eligibility)
                auto_booking_id = nb.id
                auto_booking_status = "booked"
                # Hold is now superseded by the real booking row.
                self._release_hold_from_meta(auto_booking_meta, order.id)
            except Exception:
                # Defensive fallback: payment succeeded, eligibility exists,
                # but we couldn't write the booking row. Don't roll back the
                # whole fulfillment — surface a "booking_failed" flag so the
                # frontend can route the user to the manual slot picker.
                auto_booking_status = "booking_failed"

        # ── 5. Create FittbotPayment (gateway="razorpay") ─────────────
        fittbot_payment = FittbotPayment(
            gym_id=0,
            client_id=int(customer_id),
            entitlement_id=order.id,
            source_type="fymble_purchase",
            amount_gross=order.gross_amount_minor / 100,
            amount_net=0,
            currency="INR",
            gateway="razorpay",
            gateway_payment_id=razorpay_payment_id,
            payment_method="razorpay",
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
                        "NUTRITION_PKG_RP_REWARD_ENTRIES | order=%s client=%s entries=%d msg=%s",
                        order.id, _mask(customer_id), entries_added, reward_msg,
                    )
                else:
                    logger.warning(
                        "NUTRITION_PKG_RP_REWARD_SKIP | order=%s client=%s msg=%s",
                        order.id, _mask(customer_id), reward_msg,
                    )
            except Exception as reward_exc:
                logger.warning(
                    "NUTRITION_PKG_RP_REWARD_ERROR | order=%s client=%s error=%s",
                    order.id, _mask(customer_id), reward_exc,
                )

        session.commit()

        # ── 8. Invalidate home cache ─────────────────────────────────
        self._invalidate_home_cache(int(customer_id))

        logger.info(
            "FYMBLE_PKG_RP_FULFILLED | order=%s customer=%s ent=%s elig=%s "
            "ai_diet_booking=%s sku=%s kind=%s sessions=%d credits=%d "
            "balance=%d source=%s",
            order.id,
            _mask(customer_id),
            entitlement_id,
            eligibility.id if eligibility else None,
            ai_diet_booking.id if ai_diet_booking else None,
            plan.sku,
            plan.kind.value,
            plan.total_sessions,
            credits_granted,
            new_balance,
            source,
        )

        if plan.kind == PlanKind.session_package:
            success_msg = (
                f"{plan.plan_name} purchased successfully — "
                f"{plan.total_sessions} session(s) granted"
            )
        else:
            success_msg = f"{plan.plan_name} activated"

        return RpNutritionPackageVerifyResult(
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
            booking_id=auto_booking_id,
            booking_date=auto_booking_meta["booking_date"] if auto_booking_meta else None,
            booking_start_time=auto_booking_meta["start_time"] if auto_booking_meta else None,
            booking_end_time=auto_booking_meta["end_time"] if auto_booking_meta else None,
            booking_status=auto_booking_status,
        ).model_dump()

    # ── Sync helpers ──────────────────────────────────────────────────

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

    def _sync_read_fulfillment_state(
        self, customer_id: str, *, order_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        with self._session_scope() as session:
            plan = None
            eligibility = None
            ent = None
            if order_id:
                first_item = (
                    session.query(OrderItem)
                    .filter(OrderItem.order_id == order_id)
                    .first()
                )
                if first_item:
                    plan = get_plan_or_none(first_item.sku)

                # Anchor on THIS order's entitlement, not "most recent of any
                # type" — prevents cross-plan reads.
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

            bal = (
                session.query(CreditBalance)
                .filter(CreditBalance.client_id == int(customer_id))
                .first()
            )

            return RpNutritionPackageVerifyResult(
                verified=True,
                captured=True,
                message="Verified",
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

    # ── Ad-funnel: slot validation + Redis hold ──────────────────────

    def _validate_and_hold_slot_sync(
        self,
        session: Session,
        booking,  # BookingDetails pydantic model
        order_id: str,
    ) -> Dict[str, Any]:
        """Validate the requested slot and acquire a Redis hold.

        Mirrors the conflict-resolution logic in Fymble book_slot service
        ([Fymble/nutrition_purchase_new/service.py: book_slot]), but runs
        sync inside the checkout transaction and pins one free nutritionist
        by acquiring the hold against their (date, hour, nid) key.

        Raises SlotUnavailableError (a ValueError) on conflict so the
        worker marks the command failed with a clear reason.
        """
        booking_date = date_type.fromisoformat(booking.booking_date)
        req_start = _parse_time(booking.start_time)
        req_end = _parse_time(booking.end_time)

        if booking_date <= date_type.today():
            raise SlotUnavailableError("slot_in_past_or_today")

        hint_schedule = (
            session.query(NutritionSchedule)
            .filter(
                NutritionSchedule.id == booking.schedule_id,
                NutritionSchedule.is_active.is_(True),
            )
            .first()
        )
        if not hint_schedule:
            raise SlotUnavailableError("schedule_not_found_or_inactive")

        hour_start = hint_schedule.start_time
        hour_end = hint_schedule.end_time

        if booking_date.weekday() != hint_schedule.weekday:
            raise SlotUnavailableError("booking_date_weekday_mismatch")
        if req_start < hour_start or req_end > hour_end:
            raise SlotUnavailableError("time_range_outside_schedule")

        # All schedule rows offering this exact hour-window on this weekday
        offering_rows = (
            session.query(NutritionSchedule)
            .filter(
                NutritionSchedule.weekday == hint_schedule.weekday,
                NutritionSchedule.start_time == hour_start,
                NutritionSchedule.end_time == hour_end,
                NutritionSchedule.is_active.is_(True),
                or_(
                    NutritionSchedule.start_date.is_(None),
                    NutritionSchedule.start_date <= booking_date,
                ),
                or_(
                    NutritionSchedule.end_date.is_(None),
                    NutritionSchedule.end_date >= booking_date,
                ),
            )
            .all()
        )
        if not offering_rows:
            raise SlotUnavailableError("no_active_schedule_for_window")

        active_nut_ids = {
            row[0]
            for row in session.query(Nutritionist.id)
            .filter(Nutritionist.is_active.is_(True))
            .all()
        }
        offering_rows = [
            r for r in offering_rows if r.nutritionist_id in active_nut_ids
        ]
        if not offering_rows:
            raise SlotUnavailableError("no_active_nutritionist_for_window")

        # Nutritionists with overlapping bookings on this date are burnt
        existing_bookings = (
            session.query(NutritionBooking)
            .filter(
                NutritionBooking.booking_date == booking_date,
                NutritionBooking.status.in_(["booked", "pending", "attended"]),
            )
            .all()
        )
        burnt_by_booking = {
            b.nutritionist_id
            for b in existing_bookings
            if _times_overlap(hour_start, hour_end, b.start_time, b.end_time)
        }

        # Nutritionists with an active Redis hold are also burnt
        burnt_by_hold = set()
        if self.redis is not None:
            for row in offering_rows:
                if slot_hold.is_held_sync(
                    self.redis,
                    booking_date, hour_start, hour_end,
                    row.nutritionist_id,
                ):
                    burnt_by_hold.add(row.nutritionist_id)

        burnt_ids = burnt_by_booking | burnt_by_hold
        free_rows = sorted(
            [r for r in offering_rows if r.nutritionist_id not in burnt_ids],
            key=lambda r: r.nutritionist_id,
        )
        if not free_rows:
            raise SlotUnavailableError("slot_taken")

        # Acquire the hold against the first free nutritionist. SET NX EX
        # is atomic, so two concurrent checkouts won't both grab the same
        # nutritionist — the loser falls through to the next candidate.
        if self.redis is None:
            # No Redis configured — accept the slot but skip the hold; the
            # only protection against double-booking is the DB integrity
            # check at fulfillment time.
            chosen = free_rows[0]
        else:
            chosen = None
            for row in free_rows:
                acquired = slot_hold.try_acquire_sync(
                    self.redis,
                    booking_date, hour_start, hour_end,
                    row.nutritionist_id, order_id,
                )
                if acquired:
                    chosen = row
                    break
            if chosen is None:
                raise SlotUnavailableError("slot_hold_conflict")

        return {
            "schedule_id": chosen.id,
            "nutritionist_id": chosen.nutritionist_id,
            "booking_date": booking_date.isoformat(),
            "start_time": booking.start_time,
            "end_time": booking.end_time,
            "hour_start": _fmt_time(hour_start),
            "hour_end": _fmt_time(hour_end),
        }

    def _release_hold_from_meta(
        self, booking_meta: Optional[Dict[str, Any]], order_id: str
    ) -> None:
        """Owner-checked release of a hold given a booking metadata dict.

        Safe to call when booking_meta is None or redis is unavailable.
        """
        if not booking_meta or self.redis is None:
            return
        try:
            slot_hold.release_sync(
                self.redis,
                date_type.fromisoformat(booking_meta["booking_date"]),
                _parse_time(booking_meta.get("hour_start") or booking_meta["start_time"]),
                _parse_time(booking_meta.get("hour_end") or booking_meta["end_time"]),
                int(booking_meta["nutritionist_id"]),
                order_id,
            )
        except Exception:
            # Hold release must never break the surrounding flow.
            pass

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

    async def _await_capture_marker(
        self, razorpay_payment_id: str,
    ) -> Optional[Dict[str, Any]]:
        if not self.redis or not razorpay_payment_id:
            return None

        # Match v1 dailypass defaults.
        deadline_seconds = max(
            1,
            getattr(
                self.config,
                "rp_nutrition_pkg_verify_total_timeout_seconds",
                20,
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
