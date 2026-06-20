import asyncio
import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone, date
from typing import Any, Dict, Optional, Set

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.fittbot_api.v1.payments.config.settings import get_payment_settings
from app.fittbot_api.v1.payments.models.enums import (
    ItemType,
    StatusOrder,
    StatusPayment,
    EntType,
    StatusEnt,
)
from app.fittbot_api.v1.payments.models.orders import Order, OrderItem
from app.fittbot_api.v1.payments.models.payments import Payment
from app.fittbot_api.v1.payments.models.entitlements import Entitlement
from app.fittbot_api.v1.payments.razorpay_async_gateway import (
    create_order as rzp_create_order,
    get_payment as rzp_get_payment,
)
from app.fittbot_api.v1.payments.Fittbot_Subscriptions.razorpay import process_razorpay_webhook_payload
from app.models.fittbot_models import Client
from app.models.nutrition_models import (
    NutritionEligibility,
    NutritionBooking,
    NutritionSchedule,
    Nutritionist,
)
from app.models.async_database import create_celery_async_sessionmaker
from app.models.fittbot_payments_models import Payment as FittbotPayment
from redis import Redis
from app.config.settings import settings

from ...config.database import PaymentDatabase
from .payment_event_logger import PaymentEventLogger

logger = logging.getLogger("payments.nutrition_purchase.v2.processor")
pel = PaymentEventLogger("razorpay", "nutrition_purchase")

from app.services.timezone_utils import IST

# Import legacy helpers (for non-DB operations)
from app.fittbot_api.v1.payments.routes.gym_membership import (
    _new_id,
    _mask,
    _verify_checkout_sig,
)

# Fixed price for nutrition consultation
NUTRITION_PRICE_MINOR = 149900  # ₹1499 in paise


def _mask_sensitive(s: Optional[str]) -> str:
    if not s:
        return ""
    return f"{s[:8]}...{s[-4:]}" if len(s) > 12 else "***"


# ═══════════════════════════════════════════════════════════════════════════════
# ASYNC HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════


async def _validate_schedule_and_slot(
    db: AsyncSession, schedule_id: int, booking_date: date
) -> NutritionSchedule:
    """Validate schedule exists, is active, matches weekday, and slot is available."""
    schedule = (
        await db.execute(
            select(NutritionSchedule).where(
                NutritionSchedule.id == schedule_id,
                NutritionSchedule.is_active.is_(True),
            )
        )
    ).scalars().first()

    if not schedule:
        raise HTTPException(404, "Schedule not found or not active")

    # Check weekday matches
    if booking_date.weekday() != schedule.weekday:
        raise HTTPException(400, "Booking date does not match schedule weekday")

    # Check schedule validity bounds
    if schedule.start_date and booking_date < schedule.start_date:
        raise HTTPException(400, "Booking date is before schedule start date")
    if schedule.end_date and booking_date > schedule.end_date:
        raise HTTPException(400, "Booking date is after schedule end date")

    # Cannot book today
    if booking_date <= date.today():
        raise HTTPException(400, "Cannot book for today or past dates")

    # Check slot not already booked (FOR UPDATE to prevent concurrent checkout race)
    existing_booking = (
        await db.execute(
            select(NutritionBooking).where(
                NutritionBooking.schedule_id == schedule_id,
                NutritionBooking.booking_date == booking_date,
                NutritionBooking.status.in_(["booked", "pending", "attended"]),
            ).with_for_update()
        )
    ).scalars().first()

    if existing_booking:
        raise HTTPException(409, "This slot is already booked")

    return schedule


async def _process_nutrition_purchase_item_async(
    db: AsyncSession, item: OrderItem, order: Order, payment: Payment
) -> Dict[str, Any]:
    """
    Process nutrition purchase item — creates Entitlement, NutritionEligibility, and NutritionBooking.
    This is the core function that writes to BOTH nutrition tables.
    """
    meta = item.item_metadata or {}
    booking_date_str = meta.get("booking_date")
    schedule_id = meta.get("schedule_id")
    nutritionist_id = meta.get("nutritionist_id")
    start_time = meta.get("start_time")
    end_time = meta.get("end_time")

    booking_date = date.fromisoformat(booking_date_str) if booking_date_str else date.today()

    # 1) Create Entitlement
    ent = Entitlement(
        id=_new_id("ent_"),
        order_item_id=item.id,
        customer_id=order.customer_id,
        gym_id=None,
        entitlement_type=EntType.nutrition,
        active_from=datetime.now(IST),
        active_until=datetime.now(IST) + timedelta(days=180),
        status=StatusEnt.active,
    )
    db.add(ent)
    await db.flush()

    # 2) Create NutritionEligibility (1 session, source_type=fymble_purchase)
    eligibility = NutritionEligibility(
        client_id=int(order.customer_id),
        gym_id=None,
        source_type="fymble_purchase",
        source_id=order.id,
        plan_name="Nutrition Consultation Purchase",
        plan_duration_months=0,
        total_sessions=1,
        used_sessions=0,
        remaining_sessions=1,
        granted_at=datetime.now(),
        expires_at=datetime.now() + timedelta(days=180),
    )
    db.add(eligibility)
    await db.flush()  # Need eligibility.id for booking

    logger.info(
        f"[NUTR_ELIGIBILITY_CREATED] eligibility_id={eligibility.id}, "
        f"client_id={order.customer_id}, source_id={order.id}"
    )

    # 3) Create NutritionBooking for the chosen date/slot
    # Use FOR UPDATE lock to prevent race condition between webhook and verify
    existing_slot = (
        await db.execute(
            select(NutritionBooking).where(
                NutritionBooking.schedule_id == schedule_id,
                NutritionBooking.booking_date == booking_date,
                NutritionBooking.status.in_(["booked", "pending", "attended"]),
            ).with_for_update()
        )
    ).scalars().first()

    booking_id = None
    booking_status = "booked"

    if existing_slot:
        # Check if this is the same client's booking (idempotent — already fulfilled)
        if existing_slot.client_id == int(order.customer_id):
            logger.info(
                f"[NUTR_BOOKING_EXISTS] Booking {existing_slot.id} already exists for "
                f"client {order.customer_id}, schedule {schedule_id}, date {booking_date}"
            )
            return {
                "entitlement_id": ent.id,
                "eligibility_id": eligibility.id,
                "booking_id": existing_slot.id,
                "booking_date": str(booking_date),
                "start_time": str(meta.get("start_time")),
                "end_time": str(meta.get("end_time")),
                "nutritionist_id": nutritionist_id,
                "booking_status": existing_slot.status,
                "status": "active",
            }

        # Slot taken by someone else — mark as pending for reschedule
        logger.warning(
            f"[NUTR_SLOT_TAKEN] Slot {schedule_id} on {booking_date} was booked by someone else. "
            f"Creating booking with status=pending for client {order.customer_id}"
        )
        booking_status = "pending"

    # Parse time strings back to time objects
    from datetime import time as time_type

    if isinstance(start_time, str):
        start_time_obj = datetime.strptime(start_time, "%H:%M:%S").time()
    else:
        start_time_obj = start_time

    if isinstance(end_time, str):
        end_time_obj = datetime.strptime(end_time, "%H:%M:%S").time()
    else:
        end_time_obj = end_time

    booking = NutritionBooking(
        client_id=int(order.customer_id),
        eligibility_id=eligibility.id,
        nutritionist_id=nutritionist_id,
        schedule_id=schedule_id,
        booking_date=booking_date,
        start_time=start_time_obj,
        end_time=end_time_obj,
        status=booking_status,
    )
    db.add(booking)
    await db.flush()
    booking_id = booking.id

    logger.info(
        f"[NUTR_BOOKING_CREATED] booking_id={booking_id}, eligibility_id={eligibility.id}, "
        f"date={booking_date}, status={booking_status}, client_id={order.customer_id}"
    )

    return {
        "entitlement_id": ent.id,
        "eligibility_id": eligibility.id,
        "booking_id": booking_id,
        "booking_date": str(booking_date),
        "start_time": str(start_time),
        "end_time": str(end_time),
        "nutritionist_id": nutritionist_id,
        "booking_status": booking_status,
        "status": "active",
    }


# ═══════════════════════════════════════════════════════════════════════════════
# VERIFICATION RESPONSE
# ═══════════════════════════════════════════════════════════════════════════════

class NutritionPurchaseVerificationResponse:
    def __init__(
        self,
        verified: bool,
        captured: bool,
        order_id: str,
        payment_id: str,
        service_activated: bool = False,
        service_details: Optional[Dict[str, Any]] = None,
        total_amount: int = 0,
        currency: str = "INR",
        message: str = "",
        purchased_at: Optional[datetime] = None,
    ):
        self.verified = verified
        self.captured = captured
        self.order_id = order_id
        self.payment_id = payment_id
        self.service_activated = service_activated
        self.service_details = service_details
        self.total_amount = total_amount
        self.currency = currency
        self.message = message
        self.purchased_at = purchased_at or datetime.now(IST)

    def dict(self) -> Dict[str, Any]:
        return {
            "verified": self.verified,
            "captured": self.captured,
            "order_id": self.order_id,
            "payment_id": self.payment_id,
            "service_activated": self.service_activated,
            "service_details": self.service_details,
            "total_amount": self.total_amount,
            "currency": self.currency,
            "message": self.message,
            "purchased_at": self.purchased_at.isoformat() if isinstance(self.purchased_at, datetime) else str(self.purchased_at),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# PROCESSOR
# ═══════════════════════════════════════════════════════════════════════════════

class NutritionPurchaseProcessor:
    """Runs Nutrition Purchase checkout + verification + webhook work in Celery workers."""

    def __init__(
        self,
        config,
        payment_db: PaymentDatabase,
        *,
        redis: Optional[Redis] = None,
    ):
        self.config = config
        self.payment_db = payment_db
        self.settings = get_payment_settings()
        self.redis = redis

    # ─── CHECKOUT ─────────────────────────────────────────────────────

    async def process_checkout(self, command_id: str, store) -> None:
        record = await store.mark_processing(command_id)
        payload = record.payload
        _start = time.perf_counter()
        pel.checkout_started(
            command_id=command_id,
            client_id=str(payload.get("client_id")),
        )
        try:
            result = await self._execute_checkout(payload)
        except Exception as exc:
            pel.checkout_failed(
                command_id=command_id,
                client_id=str(payload.get("client_id")),
                error_code=type(exc).__name__,
                error_detail=str(exc),
                duration_ms=int((time.perf_counter() - _start) * 1000),
            )
            logger.exception("Nutrition purchase checkout failed: %s", exc)
            await store.mark_failed(command_id, str(exc))
            return
        pel.checkout_completed(
            command_id=command_id,
            client_id=str(payload.get("client_id")),
            duration_ms=int((time.perf_counter() - _start) * 1000),
        )
        await store.mark_completed(command_id, result)

    async def _execute_checkout(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        SessionLocal = create_celery_async_sessionmaker()
        async with SessionLocal() as db:
            return await self._checkout_async(db, payload)

    async def _checkout_async(self, db: AsyncSession, payload: Dict[str, Any]) -> Dict[str, Any]:
        user_id = str(payload.get("client_id"))
        schedule_id = int(payload.get("schedule_id"))
        booking_date_str = payload.get("booking_date")

        booking_date = date.fromisoformat(booking_date_str)

        logger.info(
            f"[NUTR_CHECKOUT_START] client={user_id}, schedule={schedule_id}, "
            f"date={booking_date}"
        )

        # 1) Validate schedule and slot availability
        schedule = await _validate_schedule_and_slot(db, schedule_id, booking_date)

        # Get nutritionist info
        nutritionist = (
            await db.execute(
                select(Nutritionist).where(Nutritionist.id == schedule.nutritionist_id)
            )
        ).scalars().first()

        nutritionist_id = schedule.nutritionist_id

        logger.info(
            f"[NUTR_SLOT_VALID] schedule_id={schedule_id}, nutritionist_id={nutritionist_id}, "
            f"time={schedule.start_time}-{schedule.end_time}"
        )

        # 2) Fixed price — no reward
        grand_total = NUTRITION_PRICE_MINOR

        logger.info(f"[NUTR_GRAND_TOTAL] {grand_total / 100}rs")

        # 3) Create Order
        order_metadata = {
            "order_info": {
                "order_type": "nutrition_purchase",
                "customer_id": user_id,
                "created_at": datetime.now(IST).isoformat(),
                "currency": "INR",
                "flow": "nutrition_purchase",
            },
            "order_composition": {
                "service_type": "nutrition_consultation",
                "items_count": 1,
                "booking_date": str(booking_date),
                "schedule_id": schedule_id,
                "nutritionist_id": nutritionist_id,
            },
            "payment_summary": {
                "final_amount_minor": grand_total,
                "final_amount_rupees": grand_total / 100,
            },
        }

        order = Order(
            id=_new_id("ord_"),
            customer_id=user_id,
            provider="razorpay_pg",
            currency="INR",
            gross_amount_minor=grand_total,
            status=StatusOrder.pending,
            order_metadata=order_metadata,
        )
        db.add(order)
        await db.flush()

        logger.info(f"[NUTR_ORDER_CREATED] order_id={order.id}, grand_total={grand_total / 100}rs")

        # 4) Create OrderItem (fymble_purchase)
        item_metadata = {
            "service_type": "nutrition_consultation",
            "booking_date": str(booking_date),
            "schedule_id": schedule_id,
            "nutritionist_id": nutritionist_id,
            "amount": grand_total / 100,
            "start_time": str(schedule.start_time),
            "end_time": str(schedule.end_time),
        }

        service_item = OrderItem(
            id=_new_id("itm_"),
            order_id=order.id,
            item_type=ItemType.fymble_purchase,
            gym_id=None,
            unit_price_minor=grand_total,
            qty=1,
            item_metadata=item_metadata,
        )
        db.add(service_item)
        await db.flush()

        # 5) Create Razorpay order (no EMI — ₹299 is too low)
        pel.provider_call_started(command_id=order.id, provider_endpoint="create_order")
        _prov_start = time.perf_counter()
        try:
            rzp_order = await rzp_create_order(
                amount_minor=grand_total,
                currency="INR",
                receipt=order.id,
                notes={
                    "order_id": order.id,
                    "user_id": user_id,
                    "flow": "nutrition_purchase",
                    "service_type": "nutrition_consultation",
                    "booking_date": str(booking_date),
                    "final_amount": str(grand_total),
                },
            )
            pel.provider_call_completed(
                command_id=order.id,
                provider_endpoint="create_order",
                duration_ms=int((time.perf_counter() - _prov_start) * 1000),
            )
        except Exception as prov_exc:
            pel.provider_call_failed(
                command_id=order.id,
                provider_endpoint="create_order",
                error_code=type(prov_exc).__name__,
                duration_ms=int((time.perf_counter() - _prov_start) * 1000),
            )
            raise

        order.provider_order_id = rzp_order["id"]
        db.add(order)
        await db.commit()

        logger.info(f"[NUTR_CHECKOUT_SUCCESS] order={order.id}, rzp_order={_mask(rzp_order['id'])}")

        # Fetch client phone for Razorpay prefill
        client_row = (
            await db.execute(select(Client.contact).where(Client.client_id == int(user_id)))
        ).scalar()
        prefill = {"email": "support@fymble.app", "contact": client_row or ""}

        return {
            "razorpay_order_id": rzp_order["id"],
            "razorpay_key_id": self.settings.razorpay_key_id,
            "order_id": order.id,
            "amount_minor": grand_total,
            "currency": "INR",
            "service_type": "nutrition_consultation",
            "total_amount": grand_total,
            "booking_date": str(booking_date),
            "schedule_id": schedule_id,
            "nutritionist_name": nutritionist.full_name if nutritionist else "Nutritionist",
            "display_title": "Nutrition Consultation",
            "prefill": prefill,
        }

    # ─── VERIFY ───────────────────────────────────────────────────────

    async def process_verify(self, command_id: str, store) -> None:
        record = await store.mark_processing(command_id)
        payload_dict = record.payload
        _start = time.perf_counter()
        pel.verify_started(
            command_id=command_id,
            razorpay_payment_id=payload_dict.get("razorpay_payment_id"),
            razorpay_order_id=payload_dict.get("razorpay_order_id"),
        )
        try:
            result = await self._execute_verify(payload_dict)
        except Exception as exc:
            pel.verify_failed(
                command_id=command_id,
                error_code=type(exc).__name__,
                error_detail=str(exc),
                duration_ms=int((time.perf_counter() - _start) * 1000),
            )
            logger.exception("Nutrition purchase verification failed: %s", exc)
            await store.mark_failed(command_id, str(exc))
            return
        _dur = int((time.perf_counter() - _start) * 1000)
        if result.get("verified"):
            pel.verify_completed(
                command_id=command_id,
                verify_path="nutrition_purchase",
                duration_ms=_dur,
            )
            pel.payment_captured(
                command_id=command_id,
                razorpay_payment_id=payload_dict.get("razorpay_payment_id"),
            )
        else:
            pel.verify_failed(command_id=command_id, error_code="verify_unsuccessful", duration_ms=_dur)
        await store.mark_completed(command_id, result)

    async def _execute_verify(self, payload_dict: Dict[str, Any]) -> Dict[str, Any]:
        razorpay_payment_id = payload_dict.get("razorpay_payment_id")
        capture_marker = await self._capture_marker_snapshot(razorpay_payment_id)
        if capture_marker:
            logger.info(
                "NUTR_VERIFY_CAPTURE_CACHE_HIT",
                extra={
                    "payment_id": _mask_sensitive(razorpay_payment_id),
                    "order_id": capture_marker.get("order_id"),
                },
            )
        SessionLocal = create_celery_async_sessionmaker()
        async with SessionLocal() as db:
            return await self._verify_async(db, payload_dict, capture_marker)

    async def _verify_async(
        self,
        db: AsyncSession,
        payload_dict: Dict[str, Any],
        capture_marker: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Async business logic for nutrition purchase verification."""
        pid = payload_dict.get("razorpay_payment_id")
        oid = payload_dict.get("razorpay_order_id")
        sig = payload_dict.get("razorpay_signature")

        logger.info(f"[NUTR_VERIFY_START] payment_id={_mask_sensitive(pid)}, order_id={oid}")

        if not all([pid, oid, sig]):
            raise HTTPException(400, "Missing required fields")

        if not _verify_checkout_sig(self.settings.razorpay_key_secret, oid, pid, sig):
            pel.verify_signature_invalid(command_id=oid, razorpay_payment_id=pid)
            logger.error(f"[NUTR_VERIFY_ERROR] Invalid signature for order {oid}")
            raise HTTPException(403, "Invalid signature")

        # Find order
        order = (
            await db.execute(select(Order).where(Order.provider_order_id == oid))
        ).scalars().first()
        if not order:
            logger.error(f"[NUTR_VERIFY_ERROR] Order not found: {oid}")
            raise HTTPException(404, "Order not found")

        logger.info(f"[NUTR_ORDER_FOUND] order_id={order.id}, customer_id={order.customer_id}")

        # Get payment data from capture_marker or Razorpay API
        if capture_marker:
            payment_data = capture_marker.copy()
            payment_data.setdefault("status", "captured")
        else:
            capture_marker = await self._await_capture_marker(pid)
            if capture_marker:
                payment_data = capture_marker.copy()
                payment_data.setdefault("status", "captured")
            else:
                pel.provider_call_started(command_id=oid, provider_endpoint="get_payment")
                _prov_start = time.perf_counter()
                try:
                    payment_data = await rzp_get_payment(pid)
                    pel.provider_call_completed(
                        command_id=oid,
                        provider_endpoint="get_payment",
                        duration_ms=int((time.perf_counter() - _prov_start) * 1000),
                    )
                except Exception as prov_exc:
                    pel.provider_call_failed(
                        command_id=oid,
                        provider_endpoint="get_payment",
                        error_code=type(prov_exc).__name__,
                        duration_ms=int((time.perf_counter() - _prov_start) * 1000),
                    )
                    raise

        if payment_data.get("status") != "captured":
            logger.error(f"[NUTR_VERIFY_ERROR] Payment not captured: {payment_data.get('status')}")
            raise HTTPException(400, f"Payment not captured (status={payment_data.get('status')})")

        # Idempotency: check existing Payment
        existing_payment = (
            await db.execute(
                select(Payment).where(
                    Payment.provider_payment_id == pid,
                    Payment.status == StatusPayment.captured,
                )
            )
        ).scalars().first()

        if existing_payment:
            logger.info(f"[NUTR_EXISTING_PAYMENT] Payment {pid} already exists, checking service...")

            items = (
                await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))
            ).scalars().all()

            service_ok = False
            service_details = None

            for it in items:
                if it.item_type == ItemType.fymble_purchase:
                    existing_ent = (
                        await db.execute(
                            select(Entitlement).where(Entitlement.order_item_id == it.id)
                        )
                    ).scalars().first()

                    if existing_ent:
                        logger.info(f"[NUTR_EXISTING_SERVICE] Entitlement {existing_ent.id} already exists")
                        service_ok = True
                        service_details = {"entitlement_id": existing_ent.id, "status": "active"}
                    else:
                        # Recovery: payment exists but service missing
                        logger.warning(f"[NUTR_MISSING_SERVICE] Creating service for order {order.id}")

                        service_details = await _process_nutrition_purchase_item_async(
                            db, it, order, existing_payment
                        )

                        # Create FittbotPayment for recovery
                        fittbot_payment = FittbotPayment(
                            gym_id=0,
                            client_id=int(order.customer_id),
                            entitlement_id=order.id,
                            source_type="fymble_purchase",
                            amount_gross=existing_payment.amount_minor / 100,
                            amount_net=0,
                            currency="INR",
                            gateway="razorpay",
                            gateway_payment_id=pid,
                            payment_method=payment_data.get("method") or None,
                            is_no_cost_emi=False,
                            status="paid",
                            paid_at=datetime.now(IST),
                        )
                        db.add(fittbot_payment)

                        await db.commit()
                        service_ok = True

            return NutritionPurchaseVerificationResponse(
                verified=True,
                captured=True,
                order_id=order.id,
                payment_id=pid,
                service_activated=service_ok,
                service_details=service_details,
                total_amount=order.gross_amount_minor,
                currency="INR",
                message="Payment already processed",
            ).dict()

        # Early idempotency: check Entitlement
        early_items = (
            await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))
        ).scalars().all()

        for ei in early_items:
            if ei.item_type == ItemType.fymble_purchase:
                existing_ent_early = (
                    await db.execute(
                        select(Entitlement).where(Entitlement.order_item_id == ei.id)
                    )
                ).scalars().first()
                if existing_ent_early:
                    logger.info(
                        f"[NUTR_ALREADY_FULFILLED] Entitlement {existing_ent_early.id} already exists"
                    )
                    return NutritionPurchaseVerificationResponse(
                        verified=True,
                        captured=True,
                        order_id=order.id,
                        payment_id=pid,
                        service_activated=True,
                        service_details={"entitlement_id": existing_ent_early.id, "status": "active"},
                        total_amount=order.gross_amount_minor,
                        currency="INR",
                        message="Payment already processed",
                    ).dict()

        # ═══════════════════════════════════════════════════════════════
        # NEW PAYMENT — process everything
        # ═══════════════════════════════════════════════════════════════
        try:
            # Create Payment record (payments schema)
            pay = Payment(
                id=_new_id("pay_"),
                order_id=order.id,
                customer_id=order.customer_id,
                provider="razorpay_pg",
                provider_payment_id=pid,
                amount_minor=int(payment_data.get("amount") or order.gross_amount_minor),
                currency=payment_data.get("currency", "INR"),
                status=StatusPayment.captured,
                captured_at=datetime.now(IST),
                payment_metadata={
                    "method": payment_data.get("method"),
                    "source": "nutrition_purchase_verify",
                    "razorpay_order_id": oid,
                },
            )
            db.add(pay)
            order.status = StatusOrder.paid
            db.add(order)

            logger.info(f"[NUTR_PAYMENT_RECORDED] payment_id={pay.id}, amount={pay.amount_minor / 100}rs")

            # Create FittbotPayment (fittbot_payments schema)
            rp_method = payment_data.get("method", "")

            fittbot_payment = FittbotPayment(
                gym_id=0,  # No gym involved
                client_id=int(order.customer_id),
                entitlement_id=order.id,
                source_type="fymble_purchase",
                amount_gross=pay.amount_minor / 100,
                amount_net=0,  # Fymble keeps 100%
                currency="INR",
                gateway="razorpay",
                gateway_payment_id=pid,
                payment_method=rp_method or None,
                is_no_cost_emi=False,
                status="paid",
                paid_at=datetime.now(IST),
            )
            db.add(fittbot_payment)
            logger.info(
                f"[FITTBOT_PAYMENTS_CREATED] source_type=fymble_purchase, "
                f"entitlement_id={order.id}, method={rp_method}"
            )

            # Process the fymble_purchase item
            service_details = None
            service_ok = False

            items = (
                await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))
            ).scalars().all()

            for it in items:
                if it.item_type == ItemType.fymble_purchase:
                    logger.info(f"[NUTR_ITEM] Processing nutrition purchase item {it.id}")
                    service_details = await _process_nutrition_purchase_item_async(
                        db, it, order, pay
                    )
                    service_ok = True
                    logger.info(
                        f"[NUTR_ITEM_DONE] entitlement={service_details.get('entitlement_id')}, "
                        f"booking={service_details.get('booking_id')}"
                    )

            # Commit
            logger.info(f"[NUTR_COMMITTING] Committing transaction for order {order.id}")
            try:
                await db.commit()
            except IntegrityError as e:
                if "Duplicate entry" in str(e):
                    await db.rollback()
                    logger.info(f"[NUTR_DUPLICATE_DETECTED] Duplicate for order {order.id}")
                    return NutritionPurchaseVerificationResponse(
                        verified=True,
                        captured=True,
                        order_id=order.id,
                        payment_id=pid,
                        service_activated=service_ok,
                        service_details=service_details,
                        total_amount=order.gross_amount_minor,
                        currency="INR",
                        message="Payment already processed (duplicate)",
                    ).dict()
                raise

            logger.info(
                f"[NUTR_VERIFY_SUCCESS] order={order.id}, payment={pid}, "
                f"service_ok={service_ok}"
            )

            return NutritionPurchaseVerificationResponse(
                verified=True,
                captured=True,
                order_id=order.id,
                payment_id=pid,
                service_activated=service_ok,
                service_details=service_details,
                total_amount=order.gross_amount_minor,
                currency="INR",
                message="Payment verified and nutrition booking created",
            ).dict()

        except IntegrityError as e:
            await db.rollback()
            if "Duplicate entry" in str(e):
                logger.info(f"[NUTR_DUPLICATE] Duplicate entry for order {order.id}")
                return NutritionPurchaseVerificationResponse(
                    verified=True, captured=True, order_id=order.id, payment_id=pid,
                    service_activated=True, message="Already processed",
                ).dict()
            raise
        except Exception:
            await db.rollback()
            raise

    # ─── WEBHOOK ──────────────────────────────────────────────────────

    async def process_webhook(self, command_id: str, store) -> None:
        record = await store.mark_processing(command_id)
        payload = record.payload
        try:
            await self._persist_webhook(payload)
        except Exception as exc:
            logger.exception("Nutrition purchase webhook failed: %s", exc)
            await store.mark_failed(command_id, str(exc))
            return
        await store.mark_completed(
            command_id,
            {"event": payload.get("event"), "webhook_id": payload.get("webhook_id")},
        )

    async def _persist_webhook(self, body: Dict) -> None:
        raw = body.get("raw_body")
        signature = body.get("signature")
        if raw is None or signature is None:
            raise ValueError("webhook_signature_missing")
        raw_bytes = raw if isinstance(raw, bytes) else raw.encode("utf-8")

        with self._session_scope() as session:
            await process_razorpay_webhook_payload(raw_bytes, signature, session)
        await self._record_capture_marker(body)

        # For payment.captured events, do full fulfillment
        if body.get("event") == "payment.captured":
            await self._try_webhook_fulfillment(body)

    async def _try_webhook_fulfillment(self, body: Dict) -> None:
        """Best-effort full fulfillment from webhook payload."""
        try:
            pay_entity = body.get("payload", {}).get("payment", {}).get("entity", {})
            razorpay_order_id = pay_entity.get("order_id")
            payment_id = pay_entity.get("id")
            if not razorpay_order_id or not payment_id:
                return

            logger.info(
                "NUTR_WEBHOOK_FULFILLMENT_TRIGGERED",
                extra={
                    "razorpay_order_id": razorpay_order_id,
                    "payment_id": f"****{payment_id[-4:]}" if len(payment_id) > 4 else payment_id,
                },
            )

            payment_data = {
                "amount": pay_entity.get("amount"),
                "currency": pay_entity.get("currency"),
                "method": pay_entity.get("method"),
                "offer_id": pay_entity.get("offer_id"),
                "status": "captured",
            }

            await self.fulfill_from_webhook(razorpay_order_id, payment_id, payment_data)

        except Exception:
            logger.exception(
                "NUTR_WEBHOOK_FULFILLMENT_ERROR",
                extra={
                    "razorpay_order_id": body.get("payload", {}).get("payment", {}).get("entity", {}).get("order_id"),
                },
            )

    async def fulfill_from_webhook(
        self,
        razorpay_order_id: str,
        payment_id: str,
        payment_data: Dict[str, Any],
    ) -> None:
        """Full webhook fulfillment — mirrors _verify_async business logic."""
        SessionLocal = create_celery_async_sessionmaker()
        async with SessionLocal() as db:
            try:
                await self._fulfill_from_webhook_async(
                    db, razorpay_order_id, payment_id, payment_data
                )
            except IntegrityError as e:
                await db.rollback()
                if "Duplicate entry" in str(e):
                    logger.info(
                        "NUTR_WEBHOOK_FULFILL_ALREADY_DONE",
                        extra={
                            "razorpay_order_id": razorpay_order_id,
                            "payment_id": _mask_sensitive(payment_id),
                        },
                    )
                    return
                raise
            except Exception:
                await db.rollback()
                raise

    async def _fulfill_from_webhook_async(
        self,
        db: AsyncSession,
        razorpay_order_id: str,
        payment_id: str,
        payment_data: Dict[str, Any],
    ) -> None:
        """Inner webhook fulfillment with full business logic."""
        # 1) Find order
        order = (
            await db.execute(
                select(Order).where(Order.provider_order_id == razorpay_order_id)
            )
        ).scalars().first()
        if not order:
            logger.warning(
                "NUTR_WEBHOOK_FULFILL_ORDER_NOT_FOUND",
                extra={"razorpay_order_id": razorpay_order_id},
            )
            return

        # 2) Idempotency — if Payment already exists, skip
        existing_payment = (
            await db.execute(
                select(Payment).where(
                    Payment.provider_payment_id == payment_id,
                    Payment.status == StatusPayment.captured,
                )
            )
        ).scalars().first()
        if existing_payment:
            logger.info(
                "NUTR_WEBHOOK_FULFILL_ALREADY_PROCESSED",
                extra={
                    "razorpay_order_id": razorpay_order_id,
                    "payment_id": _mask_sensitive(payment_id),
                },
            )
            return

        # 2b) Early idempotency — if Entitlement already exists, skip
        webhook_items = (
            await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))
        ).scalars().all()
        for wi in webhook_items:
            if wi.item_type == ItemType.fymble_purchase:
                existing_ent_wh = (
                    await db.execute(
                        select(Entitlement).where(Entitlement.order_item_id == wi.id)
                    )
                ).scalars().first()
                if existing_ent_wh:
                    logger.info(
                        f"[NUTR_WEBHOOK_ALREADY_FULFILLED] Entitlement {existing_ent_wh.id} exists"
                    )
                    return

        # 3) Create Payment record
        pay = Payment(
            id=_new_id("pay_"),
            order_id=order.id,
            customer_id=order.customer_id,
            provider="razorpay_pg",
            provider_payment_id=payment_id,
            amount_minor=int(payment_data.get("amount") or order.gross_amount_minor),
            currency=payment_data.get("currency", "INR"),
            status=StatusPayment.captured,
            captured_at=datetime.now(IST),
            payment_metadata={
                "method": payment_data.get("method"),
                "source": "webhook_fulfillment_nutrition_purchase",
                "razorpay_order_id": razorpay_order_id,
            },
        )
        db.add(pay)
        order.status = StatusOrder.paid
        db.add(order)

        logger.info(
            "NUTR_WEBHOOK_FULFILL_PAYMENT_CREATED",
            extra={
                "payment_id": _mask_sensitive(payment_id),
                "order_id": order.id,
                "amount": pay.amount_minor,
            },
        )

        items = (
            await db.execute(select(OrderItem).where(OrderItem.order_id == order.id))
        ).scalars().all()


        rp_method = payment_data.get("method", "")

        fittbot_payment = FittbotPayment(
            gym_id=0,
            client_id=int(order.customer_id),
            entitlement_id=order.id,
            source_type="fymble_purchase",
            amount_gross=pay.amount_minor / 100,
            amount_net=0,
            currency="INR",
            gateway="razorpay",
            gateway_payment_id=payment_id,
            payment_method=rp_method or None,
            is_no_cost_emi=False,
            status="paid",
            paid_at=datetime.now(IST),
        )
        db.add(fittbot_payment)

        for it in items:
            if it.item_type == ItemType.fymble_purchase:
                await _process_nutrition_purchase_item_async(db, it, order, pay)
                logger.info(
                    "NUTR_WEBHOOK_FULFILL_NUTRITION_CREATED",
                    extra={"order_id": order.id, "item_id": it.id},
                )

        # 8) Commit
        await db.commit()

        logger.info(
            "NUTR_WEBHOOK_FULFILL_SUCCESS",
            extra={
                "order_id": order.id,
                "razorpay_order_id": razorpay_order_id,
                "payment_id": _mask_sensitive(payment_id),
            },
        )

    # ─── REDIS HELPERS ────────────────────────────────────────────────

    async def _capture_marker_snapshot(self, payment_id: str) -> Optional[Dict[str, Any]]:
        """Check Redis for capture marker left by webhook."""
        if not self.redis or not payment_id:
            return None
        key = f"{self.config.redis_prefix}:capture:{payment_id}"
        try:
            raw = await asyncio.to_thread(self.redis.get, key)
            if raw:
                return json.loads(raw)
        except Exception:
            logger.warning("NUTR_CAPTURE_MARKER_READ_FAILED", extra={"payment_id": _mask_sensitive(payment_id)})
        return None

    async def _await_capture_marker(self, payment_id: str) -> Optional[Dict[str, Any]]:
        """Poll Redis for capture marker with exponential backoff."""
        if not self.redis or not payment_id:
            return None
        delay = self.config.verify_db_poll_base_delay_ms / 1000
        max_delay = self.config.verify_db_poll_max_delay_ms / 1000
        deadline = time.monotonic() + self.config.verify_db_poll_total_timeout_seconds
        attempt = 0
        while True:
            attempt += 1
            marker = await self._capture_marker_snapshot(payment_id)
            if marker:
                logger.info(
                    "[NUTR_VERIFY_CAPTURE_CACHE_HIT]",
                    extra={
                        "payment_id": _mask_sensitive(payment_id),
                        "attempt": attempt,
                    },
                )
                return marker
            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(delay)
            delay = min(max_delay, delay * 1.5)
        return None

    async def _record_capture_marker(self, body: Dict[str, Any]) -> None:
        """Store capture marker in Redis for faster verify."""
        if not self.redis:
            return
        if body.get("event") != "payment.captured":
            return
        pay_entity = body.get("payload", {}).get("payment", {}).get("entity", {})
        payment_id = pay_entity.get("id")
        if not payment_id:
            return
        marker = {
            "amount": pay_entity.get("amount"),
            "currency": pay_entity.get("currency"),
            "method": pay_entity.get("method"),
            "order_id": pay_entity.get("order_id"),
            "captured_at": pay_entity.get("created_at") or int(time.time()),
        }
        key = f"{self.config.redis_prefix}:capture:{payment_id}"
        try:
            await asyncio.to_thread(
                self.redis.set,
                key,
                json.dumps(marker),
                ex=self.config.verify_capture_cache_ttl_seconds,
            )
            logger.info(
                "NUTR_WEBHOOK_CAPTURE_CACHE_SET",
                extra={
                    "payment_id": f"****{payment_id[-4:]}" if isinstance(payment_id, str) and len(payment_id) > 4 else payment_id,
                    "order_id": marker.get("order_id"),
                },
            )
        except Exception:
            logger.exception("Failed to set nutrition capture cache marker")

    @contextmanager
    def _session_scope(self):
        with self.payment_db.get_session() as session:
            yield session
