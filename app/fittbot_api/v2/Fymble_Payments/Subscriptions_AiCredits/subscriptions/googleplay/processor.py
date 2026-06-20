"""
Google Play (RevenueCat) subscription processor.

Background worker that handles order creation, purchase verification,
and webhook processing for Google Play subscriptions via RevenueCat.
"""

import asyncio
import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session
from redis import Redis

from ..._deps.config import HighConcurrencyConfig
from ..._deps.command_store import CommandStore
from ..._deps.database import PaymentDatabase, run_sync_db_operation
from ..._deps.config import get_payment_settings
from ..._deps.models import (
    CatalogProduct, Provider, SubscriptionStatus,
    Order, Payment, Subscription, WebhookProcessingLog,
)
from ..._deps.sync_service import SubscriptionSyncService
from ..._deps.revenuecat import RevenueCatAPIError, verify_purchase as rc_verify_purchase
from ..._deps.utils import (
    IST, now_ist, timedelta, mask_value, lock_query,
    generate_event_id, log_security_event, handle_billing_issues,
)
from ..._deps.event_logger import PaymentEventLogger

from ..shared.side_effects import SubscriptionSideEffects
from .schemas import GpSubscriptionOrderCommand, GpSubscriptionVerifyCommand, GpSubscriptionWebhookCommand

logger = logging.getLogger("payments.v2.subscriptions.googleplay.processor")
pel = PaymentEventLogger("revenuecat", "subscription_v2")





class GooglePlaySubscriptionProcessor:
    """Background worker for Google Play subscription lifecycle."""

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
        self.side_effects = SubscriptionSideEffects(pel)

    # ── Public entry points (called from Celery tasks) ─────────────────

    async def process_order(self, command_id: str, store: CommandStore) -> None:
        record = await store.mark_processing(command_id)
        payload = GpSubscriptionOrderCommand(**record.payload)
        _start = time.perf_counter()
        pel.checkout_started(command_id=command_id, client_id=str(payload.client_id),
                             plan_sku=payload.product_sku)
        try:
            result = await self._create_pending_order(payload)
        except Exception as exc:
            pel.checkout_failed(command_id=command_id, client_id=str(payload.client_id),
                                error_code=type(exc).__name__, error_detail=str(exc),
                                duration_ms=int((time.perf_counter() - _start) * 1000))
            logger.exception("GP subscription order failed: %s", exc)
            await store.mark_failed(command_id, str(exc))
            return
        pel.checkout_completed(command_id=command_id, client_id=str(payload.client_id),
                               duration_ms=int((time.perf_counter() - _start) * 1000),
                               plan_sku=payload.product_sku)
        pel.order_created(command_id=command_id, client_id=str(payload.client_id),
                          plan_sku=payload.product_sku)
        await store.mark_completed(command_id, result)

    async def process_verify(self, command_id: str, store: CommandStore) -> None:
        record = await store.mark_processing(command_id)
        payload = GpSubscriptionVerifyCommand(**record.payload)
        _start = time.perf_counter()
        pel.verify_started(command_id=command_id, client_id=str(payload.client_id))
        try:
            result = await self._verify_purchase(payload.client_id, command_id)
        except Exception as exc:
            pel.verify_failed(command_id=command_id, client_id=str(payload.client_id),
                              error_code=type(exc).__name__, error_detail=str(exc),
                              duration_ms=int((time.perf_counter() - _start) * 1000))
            logger.exception("GP subscription verify failed: %s", exc)
            await store.mark_failed(command_id, str(exc))
            return
        _dur = int((time.perf_counter() - _start) * 1000)
        if result.get("verified"):
            verify_path = "local_poll" if result.get("_from_local") else "revenuecat_api"
            pel.verify_completed(command_id=command_id, client_id=str(payload.client_id),
                                 verify_path=verify_path, duration_ms=_dur)
            if result.get("captured"):
                pel.payment_captured(command_id=command_id, client_id=str(payload.client_id))
        else:
            pel.verify_failed(command_id=command_id, client_id=str(payload.client_id),
                              error_code="not_verified", duration_ms=_dur,
                              error_detail=result.get("message"))
        result.pop("_from_local", None)
        await store.mark_completed(command_id, result)

    async def process_webhook(self, command_id: str, store: CommandStore) -> None:
        record = await store.mark_processing(command_id)
        payload = GpSubscriptionWebhookCommand(**record.payload)
        _start = time.perf_counter()
        pel.webhook_received(command_id=command_id)
        try:
            result = await self._handle_webhook(payload.signature, payload.raw_body, command_id)
        except Exception as exc:
            pel.webhook_failed(command_id=command_id, error_code=type(exc).__name__,
                               duration_ms=int((time.perf_counter() - _start) * 1000),
                               error_detail=str(exc))
            logger.exception("GP subscription webhook failed: %s", exc)
            await store.mark_failed(command_id, str(exc))
            return
        pel.webhook_processed(command_id=command_id,
                              duration_ms=int((time.perf_counter() - _start) * 1000),
                              event_type=result.get("event_type"), status=result.get("status"))
        await store.mark_completed(command_id, result)

    # ── Order creation ─────────────────────────────────────────────────

    async def _create_pending_order(self, payload: GpSubscriptionOrderCommand) -> Dict[str, Any]:
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
                order_id = f"ord_{ist_now.strftime('%Y%m%d')}_{payload.client_id}_{int(ist_now.timestamp())}"
                order = Order(
                    id=order_id,
                    customer_id=payload.client_id,
                    currency=payload.currency,
                    provider=Provider.google_play.value,
                    gross_amount_minor=product.base_amount_minor,
                    status="pending",
                )
                session.add(order)
                session.commit()
                session.refresh(order)

                return {
                    "order_id": order.id,
                    "client_id": payload.client_id,
                    "product_sku": payload.product_sku,
                    "amount": product.base_amount_minor,
                    "currency": payload.currency,
                    "status": "pending",
                    "api_key": self.settings.revenuecat_api_key,
                    "expires_at": (now_ist() + timedelta(minutes=15)).isoformat(),
                    "created_at": order.created_at.isoformat(),
                }

            return await run_sync_db_operation(_op)

    # ── Verify ─────────────────────────────────────────────────────────

    async def _verify_purchase(self, customer_id: str, command_id: str) -> Dict[str, Any]:
        # Step 1: Poll local DB — if webhook already processed, subscription exists.
        # Webhook already ran all side effects, so just return the status.
        local_result = await self._poll_local_confirmation(customer_id)
        if local_result:
            local_result.pop("_product_id", None)
            local_result["_from_local"] = True
            return local_result

        # Step 2: Webhook hasn't arrived — call RevenueCat API directly.
        # This path creates subscription AND runs side effects (whichever runs
        # first — this or webhook — wins the SELECT FOR UPDATE lock).
        return await self._verify_purchase_via_revenuecat(customer_id, command_id)

    async def _verify_purchase_via_revenuecat(self, customer_id: str, command_id: str) -> Dict[str, Any]:
        with self._session_scope() as session:
            def _op() -> Dict[str, Any]:
                sync_service = SubscriptionSyncService(session)
                try:
                    latest_order = (
                        session.query(Order)
                        .filter(
                            Order.customer_id == customer_id,
                            Order.provider == Provider.google_play.value,
                        )
                        .order_by(Order.created_at.desc())
                        .first()
                    )

                    pel.provider_call_started(command_id=command_id,
                                              provider_endpoint="verify_purchase")
                    _prov_start = time.perf_counter()
                    try:
                        has_active, subscription_data, error_msg = rc_verify_purchase(
                            app_user_id=customer_id,
                            api_key=self.settings.revenuecat_api_key,
                        )
                        pel.provider_call_completed(
                            command_id=command_id,
                            provider_endpoint="verify_purchase",
                            duration_ms=int((time.perf_counter() - _prov_start) * 1000),
                        )
                    except Exception as prov_exc:
                        pel.provider_call_failed(
                            command_id=command_id,
                            provider_endpoint="verify_purchase",
                            error_code=type(prov_exc).__name__,
                            duration_ms=int((time.perf_counter() - _prov_start) * 1000),
                        )
                        raise

                    if not has_active:
                        friendly_message = (
                            error_msg
                            or "No active Google Play subscription found. Please retry in a few seconds."
                        )
                        return {
                            "verified": False,
                            "captured": False,
                            "subscription_active": False,
                            "has_premium": False,
                            "message": friendly_message,
                            "order_id": latest_order.id if latest_order else None,
                            "order_status": latest_order.status if latest_order else None,
                            "order_created_at": latest_order.created_at.isoformat()
                            if latest_order and latest_order.created_at
                            else None,
                        }

                    # ── Extract subscription data from RevenueCat ──────
                    rc_period_type = (subscription_data.get("period_type") or "").upper()
                    is_trial = rc_period_type == "TRIAL"

                    price_info = subscription_data.get("price") or {}
                    price_amount = price_info.get("amount")
                    price_currency = price_info.get("currency") or "INR"
                    price_minor: Optional[int] = None
                    if price_amount is not None:
                        try:
                            price_minor = int(round(float(price_amount) * 100))
                        except (TypeError, ValueError):
                            price_minor = None

                    base_product_id = subscription_data.get("product_identifier", "unknown")
                    plan_identifier = (
                        subscription_data.get("product_plan_identifier")
                        or subscription_data.get("base_plan_identifier")
                        or subscription_data.get("base_plan_id")
                    )
                    if plan_identifier and ":" not in base_product_id:
                        product_id = f"{base_product_id}:{plan_identifier}"
                    else:
                        product_id = base_product_id

                    rc_purchased_date = subscription_data.get("original_purchase_date")
                    rc_expires_date = subscription_data.get("expires_date")

                    txn_candidates = [
                        subscription_data.get("original_transaction_id"),
                        subscription_data.get("original_transaction_identifier"),
                        subscription_data.get("original_store_transaction_id"),
                        subscription_data.get("original_external_purchase_id"),
                        subscription_data.get("transaction_id"),
                        subscription_data.get("store_transaction_id"),
                    ]
                    rc_original_txn_id = next((val for val in txn_candidates if val), None)
                    store_transaction_id = subscription_data.get(
                        "store_transaction_id",
                        f"rc_{customer_id}_{int(now_ist().timestamp())}",
                    )
                    rc_original_txn_id = rc_original_txn_id or store_transaction_id

                    if not rc_purchased_date or not rc_expires_date:
                        logger.error(
                            "GP_VERIFY_MISSING_DATES customer=%s purchased=%s expires=%s",
                            mask_value(customer_id), rc_purchased_date, rc_expires_date,
                        )
                        return {
                            "verified": False,
                            "captured": False,
                            "subscription_active": False,
                            "has_premium": False,
                            "message": "Subscription dates missing from provider. Please retry.",
                        }

                    purchased_date = datetime.fromisoformat(
                        rc_purchased_date.replace("Z", "+00:00")
                    ).astimezone(IST)
                    expires_date = datetime.fromisoformat(
                        rc_expires_date.replace("Z", "+00:00")
                    ).astimezone(IST)

                    # ── Find or create subscription ────────────────────
                    existing_subscription = self._find_existing_subscription(
                        session, customer_id, product_id, base_product_id,
                        store_transaction_id, rc_original_txn_id,
                    )

                    already_active = (
                        existing_subscription is not None
                        and existing_subscription.status in ["active", "trial", "renewed"]
                    )

                    pending_order = lock_query(
                        session.query(Order)
                        .filter(
                            Order.customer_id == customer_id,
                            Order.status == "pending",
                            Order.provider == Provider.google_play.value,
                        )
                        .order_by(Order.created_at.desc())
                    ).first()

                    latest_order_local = pending_order or latest_order
                    amount_minor = pending_order.gross_amount_minor if pending_order else None
                    if amount_minor in (None, 0) and price_minor is not None:
                        amount_minor = price_minor

                    if pending_order:
                        pending_order.status = "paid"
                        pending_order.provider_order_id = store_transaction_id
                        if price_minor is not None and pending_order.gross_amount_minor in (None, 0):
                            pending_order.gross_amount_minor = price_minor
                        if price_currency:
                            pending_order.currency = price_currency
                        session.add(pending_order)
                    else:
                        log_security_event(
                            "ORDER_NOT_FOUND_ON_VERIFY",
                            {
                                "customer_id": mask_value(customer_id),
                                "store_transaction_id": mask_value(store_transaction_id)
                                if store_transaction_id else None,
                            },
                        )
                        amount_minor = amount_minor or 0

                    sub_status = "trial" if is_trial else "active"

                    if existing_subscription:
                        subscription = existing_subscription
                        subscription.product_id = product_id
                        subscription.status = sub_status
                        subscription.rc_original_txn_id = rc_original_txn_id
                        subscription.latest_txn_id = store_transaction_id
                        subscription.active_from = purchased_date
                        subscription.active_until = expires_date
                        subscription.auto_renew = True
                        if is_trial:
                            subscription.trial_start = purchased_date
                            subscription.trial_end = expires_date
                        session.add(subscription)
                    else:
                        subscription_id = sync_service.generate_id("sub")
                        subscription = Subscription(
                            id=subscription_id,
                            customer_id=customer_id,
                            product_id=product_id,
                            provider=Provider.google_play.value,
                            status=sub_status,
                            rc_original_txn_id=rc_original_txn_id,
                            latest_txn_id=store_transaction_id,
                            active_from=purchased_date,
                            active_until=expires_date,
                            auto_renew=True,
                            trial_start=purchased_date if is_trial else None,
                            trial_end=expires_date if is_trial else None,
                        )
                        session.add(subscription)
                        session.flush()

                    payment_id: Optional[str] = None
                    if not is_trial and pending_order:
                        # Only create payment for paid subscriptions
                        payment_id = sync_service.generate_id("pay")
                        payment = Payment(
                            id=payment_id,
                            order_id=pending_order.id,
                            customer_id=customer_id,
                            provider=Provider.google_play.value,
                            provider_payment_id=store_transaction_id,
                            amount_minor=amount_minor or 0,
                            currency=price_currency,
                            status="captured",
                            payment_metadata={
                                "source": "verify_endpoint_v2",
                                "verified_at": now_ist().isoformat(),
                            },
                        )
                        session.add(payment)

                    if is_trial:
                        # Trial: grant 5 credits only
                        self.side_effects.grant_trial_credits(
                            session,
                            customer_id=customer_id,
                            subscription_id=subscription.id,
                            trial_end=expires_date,
                            command_id=command_id,
                        )
                    else:
                        # Paid: grant 100 credits + nutrition
                        self.side_effects.grant_subscription_credits(
                            session,
                            customer_id=customer_id,
                            subscription_id=subscription.id,
                            transaction_id=store_transaction_id or "",
                            command_id=command_id,
                        )
                        try:
                            self.side_effects.grant_nutrition_if_eligible(
                                session,
                                customer_id=customer_id,
                                subscription_id=subscription.id,
                                product_id=product_id,
                                command_id=command_id,
                            )
                        except Exception as nutr_exc:
                            pel.side_effect_failed(command_id=command_id,
                                                   side_effect="nutrition", error_detail=str(nutr_exc),
                                                   client_id=customer_id)
                            logger.warning("[NUTRITION_ELIGIBILITY_ERROR] %s", nutr_exc)

                    session.commit()

                    if is_trial:
                        response_message = "Free trial activated"
                    elif already_active:
                        response_message = "Purchase already verified"
                    else:
                        response_message = "Purchase verified - Premium activated"

                    return {
                        "verified": True,
                        "captured": not is_trial,
                        "subscription_active": True,
                        "has_premium": True,
                        "is_trial": is_trial,
                        "message": response_message,
                        "subscription_id": subscription.id,
                        "payment_id": payment_id,
                        "order_id": pending_order.id
                        if pending_order
                        else (latest_order_local.id if latest_order_local else None),
                        "active_from": subscription.active_from.isoformat()
                        if subscription.active_from else None,
                        "active_until": subscription.active_until.isoformat()
                        if subscription.active_until else None,
                        "trial_end": subscription.trial_end.isoformat()
                        if subscription.trial_end else None,
                        "auto_renew": True,
                        "_is_new_subscription": not already_active,
                        "_is_trial": is_trial,
                        "_customer_id": customer_id,
                        "_product_id": product_id,
                    }
                except RevenueCatAPIError as rc_error:
                    session.rollback()
                    raise rc_error
                except Exception:
                    session.rollback()
                    raise

            result = await run_sync_db_operation(_op)

            # Reward entries + referral (best-effort, paid only)
            if result.get("verified") and result.get("_is_new_subscription") and not result.get("_is_trial"):
                _cid = result.get("_customer_id")
                await self.side_effects.add_reward_entries(
                    customer_id=_cid,
                    subscription_id=result.get("subscription_id", ""),
                    command_id=command_id,
                )
                await self.side_effects.credit_referrer_if_yearly(
                    customer_id=_cid,
                    subscription_id=result.get("subscription_id", ""),
                    plan_name=result.get("_product_id", ""),
                    command_id=command_id,
                )

            result.pop("_is_new_subscription", None)
            result.pop("_is_trial", None)
            result.pop("_customer_id", None)
            result.pop("_product_id", None)
            return result

    # ── Webhook ────────────────────────────────────────────────────────

    async def _handle_webhook(self, signature: str, raw_body: str, command_id: str) -> Dict[str, Any]:
        if signature != self.settings.revenuecat_webhook_secret:
            pel.webhook_signature_invalid(command_id=command_id)
            log_security_event(
                "INVALID_WEBHOOK_SIGNATURE",
                {"signature_prefix": mask_value(signature, left=8, right=0), "source": "revenuecat_v2"},
            )
            raise ValueError("invalid_webhook_signature")

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            log_security_event("WEBHOOK_INVALID_JSON", {"error": str(exc), "source": "revenuecat_v2"})
            raise

        event = payload.get("event", {})

        with self._session_scope() as session:
            def _op() -> Dict[str, Any]:
                sync_service = SubscriptionSyncService(session)
                try:
                    customer_id = event.get("app_user_id")
                    event_type = event.get("type")

                    if not customer_id:
                        log_security_event(
                            "WEBHOOK_MISSING_CUSTOMER_ID",
                            {"event_type": event_type, "event_id": event.get("id")},
                        )
                        return {"status": "ignored", "reason": "missing_customer_id"}

                    event_id = generate_event_id(event)
                    should_process, existing_log = sync_service.check_idempotency(
                        event_id, event_type, allow_retry_on_failure=True
                    )

                    if not should_process:
                        return {
                            "status": "already_processed",
                            "event_id": event_id,
                            "processing_status": existing_log.status if existing_log else None,
                        }

                    if existing_log:
                        processing_log = existing_log
                    else:
                        processing_log = WebhookProcessingLog(
                            id=sync_service.generate_id("whl"),
                            event_id=event_id,
                            event_type=event_type,
                            customer_id=customer_id,
                            status="processing",
                            started_at=now_ist(),
                            raw_event_data=json.dumps(event),
                            is_recovery_event=event.get("_is_recovery", False),
                        )
                        session.add(processing_log)
                        session.flush()

                    result = self._route_webhook_event(
                        session, sync_service, event, event_type,
                        customer_id, processing_log, command_id,
                    )

                    if result.get("success"):
                        session.commit()
                        return {
                            "status": "processed",
                            "event_type": event_type,
                            "event_id": event_id,
                            "result": result,
                            "_customer_id": customer_id,
                        }

                    log_security_event(
                        "WEBHOOK_PROCESSING_FAILED",
                        {
                            "event_type": event_type,
                            "event_id": event_id,
                            "customer_id": mask_value(customer_id),
                            "error": result.get("error"),
                        },
                    )
                    session.rollback()
                    raise ValueError(f"Processing failed: {result.get('error')}")

                except Exception:
                    session.rollback()
                    raise

            result = await run_sync_db_operation(_op)

        if result.get("status") == "processed":
            await self._record_capture_marker(event)

            # Async side effects (rewards, referral) — run outside DB session.
            # These are best-effort and idempotent.
            _wh_cid = result.get("_customer_id")
            _inner = result.get("result", {})
            _sub_id = _inner.get("subscription_id", "")
            _is_trial = _inner.get("is_trial", False)
            _event_type = result.get("event_type")
            _trial_converted = _inner.get("trial_converted", False)

            # Rewards + referral run ONLY on first paid subscription:
            # - Paid INITIAL_PURCHASE (not trial)
            # - RENEWAL that converts trial to paid
            should_run_rewards = (
                (_event_type == "INITIAL_PURCHASE" and not _is_trial)
                or (_event_type == "RENEWAL" and _trial_converted)
            )
            if should_run_rewards and _wh_cid:
                await self.side_effects.add_reward_entries(
                    customer_id=_wh_cid,
                    subscription_id=_sub_id,
                    command_id=command_id,
                )
                await self.side_effects.credit_referrer_if_yearly(
                    customer_id=_wh_cid,
                    subscription_id=_sub_id,
                    plan_name=event.get("product_id", ""),
                    command_id=command_id,
                )

        result.pop("_customer_id", None)
        return result

    def _route_webhook_event(
        self,
        session: Session,
        sync_service: SubscriptionSyncService,
        event: Dict[str, Any],
        event_type: str,
        customer_id: str,
        processing_log: WebhookProcessingLog,
        command_id: str,
    ) -> Dict[str, Any]:
        if event_type == "INITIAL_PURCHASE":
            result = sync_service.process_initial_purchase(event, processing_log)
            if result.get("success"):
                is_trial = result.get("is_trial", False)
                subscription_id = result.get("subscription_id", event.get("store_transaction_id"))
                product_id = event.get("product_id", "")

                if is_trial:
                    # ── TRIAL: grant 5 credits only, no payment/nutrition/rewards ──
                    expiration_at_ms = event.get("expiration_at_ms")
                    trial_end = None
                    if expiration_at_ms:
                        trial_end = datetime.fromtimestamp(
                            expiration_at_ms / 1000, tz=IST
                        )
                    self.side_effects.grant_trial_credits(
                        session,
                        customer_id=customer_id,
                        subscription_id=subscription_id,
                        trial_end=trial_end,
                        command_id=command_id,
                    )
                    logger.info("GP_WEBHOOK_TRIAL_PURCHASE customer=%s sub=%s", customer_id, subscription_id)
                else:
                    # ── PAID: full side effects (100 credits + nutrition + rewards + referral) ──
                    # Grant 100 subscription credits
                    self.side_effects.grant_subscription_credits(
                        session,
                        customer_id=customer_id,
                        subscription_id=subscription_id,
                        transaction_id=event.get("transaction_id", ""),
                        command_id=command_id,
                    )
                    # Grant nutrition eligibility
                    try:
                        self.side_effects.grant_nutrition_if_eligible(
                            session,
                            customer_id=customer_id,
                            subscription_id=subscription_id,
                            product_id=product_id,
                            command_id=command_id,
                        )
                    except Exception as nutr_exc:
                        pel.side_effect_failed(command_id=command_id,
                                               side_effect="nutrition", error_detail=str(nutr_exc),
                                               client_id=customer_id)
                        logger.warning("[NUTRITION_ELIGIBILITY_ERROR] Webhook: %s", nutr_exc)
            return result

        elif event_type == "RENEWAL":
            result = sync_service.process_renewal(event, processing_log)
            if result.get("success"):
                subscription_id = result.get("subscription_id", "")
                was_trial = result.get("trial_converted", False)
                product_id = event.get("product_id", "")

                # Every renewal: 100 credits (deduped by transaction_id)
                self.side_effects.grant_subscription_credits(
                    session,
                    customer_id=customer_id,
                    subscription_id=subscription_id,
                    transaction_id=event.get("transaction_id", ""),
                    command_id=command_id,
                )

                # Nutrition: grant on trial-to-paid conversion (first real payment)
                if was_trial:
                    try:
                        self.side_effects.grant_nutrition_if_eligible(
                            session,
                            customer_id=customer_id,
                            subscription_id=subscription_id,
                            product_id=product_id,
                            command_id=command_id,
                        )
                    except Exception as nutr_exc:
                        pel.side_effect_failed(command_id=command_id,
                                               side_effect="nutrition", error_detail=str(nutr_exc),
                                               client_id=customer_id)
                        logger.warning("[NUTRITION_ELIGIBILITY_ERROR] Renewal: %s", nutr_exc)
            return result

        elif event_type == "CANCELLATION":
            return sync_service.process_cancellation(event, processing_log)
        elif event_type == "EXPIRATION":
            return sync_service.process_expiration(event, processing_log)
        elif event_type == "BILLING_ISSUES":
            return handle_billing_issues(event, session, processing_log)
        else:
            processing_log.status = "ignored"
            processing_log.completed_at = now_ist()
            processing_log.result_summary = f"Unhandled event type: {event_type}"
            session.commit()
            return {"status": "ignored", "reason": f"unhandled_event_type: {event_type}"}

    # ── Local polling ──────────────────────────────────────────────────

    async def _poll_local_confirmation(self, customer_id: str) -> Optional[Dict[str, Any]]:
        delay = max(0.2, self.config.revenuecat_verify_poll_base_delay_ms / 1000)
        max_delay = self.config.revenuecat_verify_poll_max_delay_ms / 1000
        deadline = time.monotonic() + max(1, self.config.revenuecat_verify_total_timeout_seconds)
        max_attempts = max(1, self.config.revenuecat_verify_poll_attempts)
        attempt = 0

        while time.monotonic() < deadline and attempt < max_attempts:
            attempt += 1

            capture_marker = await self._capture_marker_snapshot(customer_id)
            if capture_marker:
                logger.info("GP_SUB_VERIFY_CAPTURE_CACHE_HIT customer=%s attempt=%s",
                            mask_value(customer_id), attempt)
                local_snapshot = await self._fetch_local_verification_payload(customer_id)
                if local_snapshot:
                    return local_snapshot

            local_snapshot = await self._fetch_local_verification_payload(customer_id)
            if local_snapshot:
                logger.info("GP_SUB_VERIFY_LOCAL_FOUND customer=%s attempt=%s",
                            mask_value(customer_id), attempt)
                return local_snapshot

            await asyncio.sleep(delay)
            delay = min(delay * 1.5, max_delay)

        return None

    async def _fetch_local_verification_payload(self, customer_id: str) -> Optional[Dict[str, Any]]:
        with self._session_scope() as session:
            def _op() -> Optional[Dict[str, Any]]:
                return self._local_verification_payload(session, customer_id)
            return await run_sync_db_operation(_op)

    def _local_verification_payload(self, session: Session, customer_id: str) -> Optional[Dict[str, Any]]:
        now = now_ist()
        subscription = (
            session.query(Subscription)
            .filter(
                Subscription.customer_id == customer_id,
                Subscription.provider == Provider.google_play.value,
                Subscription.status.in_([
                    SubscriptionStatus.active.value,
                    SubscriptionStatus.trial.value,
                    SubscriptionStatus.renewed.value,
                ]),
            )
            .order_by(Subscription.created_at.desc())
            .first()
        )

        if not subscription:
            return None

        active_until = subscription.active_until
        if active_until:
            if active_until.tzinfo is None:
                from datetime import timezone
                active_until = active_until.replace(tzinfo=timezone.utc)
            if active_until < now:
                return None

        payment: Optional[Payment] = None
        if subscription.latest_txn_id:
            payment = (
                session.query(Payment)
                .filter(
                    Payment.provider == Provider.google_play.value,
                    Payment.provider_payment_id == subscription.latest_txn_id,
                )
                .order_by(Payment.created_at.desc())
                .first()
            )

        order: Optional[Order] = None
        if payment and payment.order_id:
            order = session.query(Order).filter(Order.id == payment.order_id).first()
        if not order:
            order = (
                session.query(Order)
                .filter(
                    Order.customer_id == customer_id,
                    Order.provider == Provider.google_play.value,
                )
                .order_by(Order.created_at.desc())
                .first()
            )

        is_trial = subscription.status == SubscriptionStatus.trial.value
        return {
            "verified": True,
            "captured": not is_trial,
            "subscription_active": True,
            "has_premium": True,
            "is_trial": is_trial,
            "message": "Free trial active" if is_trial else "Subscription verified via webhook",
            "subscription_id": subscription.id,
            "payment_id": payment.provider_payment_id if payment else subscription.latest_txn_id,
            "order_id": order.id if order else None,
            "active_from": subscription.active_from.isoformat() if subscription.active_from else None,
            "active_until": subscription.active_until.isoformat() if subscription.active_until else None,
            "trial_end": subscription.trial_end.isoformat() if subscription.trial_end else None,
            "auto_renew": bool(subscription.auto_renew),
            "_product_id": subscription.product_id or "",
        }

    # ── Helpers ────────────────────────────────────────────────────────

    def _find_existing_subscription(
        self,
        session: Session,
        customer_id: str,
        product_id: str,
        base_product_id: str,
        store_transaction_id: Optional[str],
        rc_original_txn_id: Optional[str],
    ) -> Optional[Subscription]:
        existing = None
        if store_transaction_id:
            existing = lock_query(
                session.query(Subscription).filter(
                    Subscription.provider == Provider.google_play.value,
                    Subscription.latest_txn_id == store_transaction_id,
                )
            ).first()

        if not existing and rc_original_txn_id:
            existing = lock_query(
                session.query(Subscription).filter(
                    Subscription.provider == Provider.google_play.value,
                    Subscription.rc_original_txn_id == rc_original_txn_id,
                )
            ).first()

        if not existing:
            possible_products = [product_id]
            if base_product_id and base_product_id != product_id:
                possible_products.append(base_product_id)
            existing = lock_query(
                session.query(Subscription)
                .filter(
                    Subscription.customer_id == customer_id,
                    Subscription.product_id.in_(possible_products),
                    Subscription.provider == Provider.google_play.value,
                    Subscription.status.in_([
                        SubscriptionStatus.active.value,
                        SubscriptionStatus.trial.value,
                        SubscriptionStatus.renewed.value,
                    ]),
                )
                .order_by(Subscription.created_at.desc())
            ).first()

        return existing

    @contextmanager
    def _session_scope(self):
        with self.payment_db.get_session() as session:
            yield session

    def _capture_cache_key(self, customer_id: str) -> str:
        prefix = self.config.revenuecat_redis_prefix or self.config.redis_prefix
        return f"{prefix}:sub_capture:{customer_id}"

    async def _capture_marker_snapshot(self, customer_id: str) -> Optional[Dict[str, Any]]:
        if not self.redis or not customer_id:
            return None
        key = self._capture_cache_key(customer_id)
        raw = await asyncio.to_thread(self.redis.get, key)
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def _record_capture_marker(self, event: Dict[str, Any]) -> None:
        if not self.redis:
            return
        customer_id = event.get("app_user_id")
        if not customer_id:
            return
        marker = {
            "event_type": event.get("type"),
            "product_id": event.get("product_id"),
            "store_transaction_id": event.get("store_transaction_id"),
            "purchased_at_ms": event.get("purchased_at_ms"),
            "expiration_at_ms": event.get("expiration_at_ms"),
        }
        key = self._capture_cache_key(customer_id)
        try:
            await asyncio.to_thread(
                self.redis.set, key, json.dumps(marker),
                ex=self.config.revenuecat_capture_cache_ttl_seconds,
            )
            logger.info("GP_SUB_CAPTURE_CACHE_SET customer=%s event=%s",
                        mask_value(customer_id), marker.get("event_type"))
        except Exception:
            logger.exception("Failed to set GP subscription capture cache")
