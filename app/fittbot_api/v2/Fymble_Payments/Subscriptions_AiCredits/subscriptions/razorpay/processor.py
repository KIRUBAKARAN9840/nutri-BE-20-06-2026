"""
Razorpay subscription processor — background Celery worker.

Mirrors v1 SubscriptionProcessor + WebhookProcessor with v2 OOP structure:
  - process_checkout → Create Razorpay subscription + pending Order
  - process_verify   → Signature check → poll local → fall back to Razorpay API
  - process_webhook  → Handle payment.captured events for subscriptions
"""

import asyncio
import json
import logging
import random
import time
from contextlib import contextmanager
from typing import Any, Dict, Optional

import httpx
from sqlalchemy import or_
from sqlalchemy.orm import Session
from redis import Redis

from ..._deps.config import HighConcurrencyConfig, get_payment_settings
from ..._deps.command_store import CommandStore
from ..._deps.database import PaymentDatabase, run_sync_db_operation
from ..._deps.models import CatalogProduct, Payment, Subscription, Order, OrderItem
from ..._deps.razorpay import (
    rzp_create_subscription,
    rzp_get_plan,
    rzp_get_payment,
    create_or_update_subscription_pending,
    create_pending_order,
    process_razorpay_webhook_payload,
    legacy_rzp,
)
from ..._deps.event_logger import PaymentEventLogger

from ..shared.side_effects import SubscriptionSideEffects
from .schemas import (
    RpSubscriptionCheckoutCommand,
    RpSubscriptionVerifyCommand,
    RpSubscriptionWebhookCommand,
)

logger = logging.getLogger("payments.v2.subscriptions.razorpay.processor")
pel = PaymentEventLogger("razorpay", "subscription_v2")


class RazorpaySubscriptionProcessor:
    """Background worker for Razorpay subscription lifecycle."""

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
        self._provider_semaphore = asyncio.Semaphore(config.max_provider_concurrency)
        self.redis = redis
        self.side_effects = SubscriptionSideEffects(pel)

    # ── Public entry points (called from Celery tasks) ────────────────

    async def process_checkout(self, command_id: str, store: CommandStore) -> None:
        record = await store.mark_processing(command_id)
        payload = RpSubscriptionCheckoutCommand(command_id=command_id, **record.payload)
        _start = time.perf_counter()
        pel.checkout_started(
            command_id=command_id,
            client_id=payload.user_id,
            plan_sku=payload.plan_sku,
        )
        try:
            result = await self._execute_checkout(payload)
        except Exception as exc:
            pel.checkout_failed(
                command_id=command_id,
                client_id=payload.user_id,
                error_code=type(exc).__name__,
                error_detail=str(exc),
                duration_ms=int((time.perf_counter() - _start) * 1000),
                plan_sku=payload.plan_sku,
            )
            logger.exception("RP subscription checkout failed", extra={"command_id": command_id})
            await store.mark_failed(command_id, str(exc))
            return
        pel.checkout_completed(
            command_id=command_id,
            client_id=payload.user_id,
            duration_ms=int((time.perf_counter() - _start) * 1000),
            plan_sku=payload.plan_sku,
        )
        await store.mark_completed(command_id, result)

    async def process_verify(self, command_id: str, store: CommandStore) -> None:
        record = await store.mark_processing(command_id)
        payload = RpSubscriptionVerifyCommand(command_id=command_id, **record.payload)
        _start = time.perf_counter()
        pel.verify_started(
            command_id=command_id,
            client_id=payload.user_id,
            razorpay_payment_id=payload.razorpay_payment_id,
            razorpay_subscription_id=payload.razorpay_subscription_id,
        )
        try:
            result = await self._execute_verify(payload, command_id)
        except Exception as exc:
            pel.verify_failed(
                command_id=command_id,
                client_id=payload.user_id,
                error_code=type(exc).__name__,
                error_detail=str(exc),
                duration_ms=int((time.perf_counter() - _start) * 1000),
            )
            logger.exception("RP subscription verify failed", extra={"command_id": command_id})
            await store.mark_failed(command_id, str(exc))
            return
        await store.mark_completed(command_id, result)

    async def process_webhook(self, command_id: str, store: CommandStore) -> None:
        record = await store.mark_processing(command_id)
        payload = record.payload
        _start = time.perf_counter()
        pel.webhook_received(command_id=command_id, event_type=payload.get("event"))
        try:
            await self._persist_webhook(payload, command_id)
        except Exception as exc:
            pel.webhook_failed(
                command_id=command_id,
                error_code=type(exc).__name__,
                duration_ms=int((time.perf_counter() - _start) * 1000),
                event_type=payload.get("event"),
                error_detail=str(exc),
            )
            logger.exception("RP subscription webhook failed", extra={"command_id": command_id})
            await store.mark_failed(command_id, str(exc))
            return
        pel.webhook_processed(
            command_id=command_id,
            duration_ms=int((time.perf_counter() - _start) * 1000),
            event_type=payload.get("event"),
        )
        await store.mark_completed(
            command_id,
            {"event": payload.get("event"), "webhook_id": payload.get("webhook_id")},
        )

    # ── Checkout ──────────────────────────────────────────────────────

    async def _execute_checkout(self, command: RpSubscriptionCheckoutCommand) -> Dict[str, Any]:
        with self._session_scope() as session:
            catalog = await self._fetch_catalog(session, command.plan_sku)
            if not catalog or not catalog.razorpay_plan_id:
                raise ValueError("invalid_plan_sku")

            plan = await self._maybe_fetch_plan(catalog.razorpay_plan_id)
            total_count = self._resolve_total_count(plan)

            notes = {
                "plan_sku": command.plan_sku,
                "customer_id": command.user_id,
                "flow": "subscription_razorpay_v2",
            }
            notes.update({k: str(v) for k, v in (command.metadata or {}).items()})

            _prov_start = time.perf_counter()
            pel.provider_call_started(
                command_id=command.command_id,
                provider_endpoint="create_subscription",
            )
            try:
                subscription = await self._provider_call(
                    rzp_create_subscription(
                        catalog.razorpay_plan_id,
                        notes=notes,
                        total_count=total_count,
                    )
                )
            except Exception as exc:
                pel.provider_call_failed(
                    command_id=command.command_id,
                    provider_endpoint="create_subscription",
                    error_code=type(exc).__name__,
                    duration_ms=int((time.perf_counter() - _prov_start) * 1000),
                )
                raise
            pel.provider_call_completed(
                command_id=command.command_id,
                provider_endpoint="create_subscription",
                duration_ms=int((time.perf_counter() - _prov_start) * 1000),
            )
            sub_id = subscription["id"]

            try:
                order = await run_sync_db_operation(
                    lambda: create_pending_order(
                        session,
                        user_id=command.user_id,
                        amount_minor=catalog.base_amount_minor,
                        sub_id=sub_id,
                        sku=catalog.sku,
                        title=catalog.title,
                    )
                )
                pel.order_created(
                    command_id=command.command_id,
                    client_id=command.user_id,
                    razorpay_subscription_id=sub_id,
                    plan_sku=catalog.sku,
                )
                await run_sync_db_operation(
                    lambda: create_or_update_subscription_pending(
                        session,
                        user_id=command.user_id,
                        plan_sku=catalog.sku,
                        provider_subscription_id=sub_id,
                    )
                )
                await run_sync_db_operation(session.commit)
            except Exception:
                await run_sync_db_operation(session.rollback)
                raise

            return {
                "subscription_id": sub_id,
                "order_id": getattr(order, "id", None),
                "razorpay_key_id": self.settings.razorpay_key_id,
                "display_title": catalog.title,
            }

    # ── Verify ────────────────────────────────────────────────────────

    async def _execute_verify(
        self, command: RpSubscriptionVerifyCommand, command_id: str
    ) -> Dict[str, Any]:
        pid = command.razorpay_payment_id
        sid = command.razorpay_subscription_id
        sig = command.razorpay_signature
        user_id = command.user_id

        _verify_start = time.perf_counter()

        # Signature validation
        if not legacy_rzp.verify_checkout_subscription_sig(
            self.settings.razorpay_key_secret, pid, sid, sig
        ):
            await legacy_rzp.log_security_event(
                "INVALID_SIGNATURE",
                {"payment_id": legacy_rzp._mask(pid), "sub_id": legacy_rzp._mask(sid)},
            )
            pel.verify_signature_invalid(
                command_id=command_id,
                client_id=user_id,
                razorpay_payment_id=pid,
                razorpay_subscription_id=sid,
                duration_ms=int((time.perf_counter() - _verify_start) * 1000),
            )
            return {"verified": False, "captured": False, "error": "invalid_signature"}

        # Poll local confirmation (cache + DB)
        local_result = await self._poll_local_confirmation(command)
        if local_result:
            _dur = int((time.perf_counter() - _verify_start) * 1000)
            pel.verify_completed(
                command_id=command_id,
                verify_path="cache_or_db_poll",
                client_id=user_id,
                duration_ms=_dur,
                razorpay_payment_id=pid,
                razorpay_subscription_id=sid,
            )
            pel.payment_captured(
                command_id=command_id,
                client_id=user_id,
                razorpay_payment_id=pid,
                razorpay_subscription_id=sid,
            )
            await self._run_verify_side_effects(
                session_scope=True,
                sid=sid,
                user_id=user_id,
                command_id=command_id,
            )
            return local_result

        # Fallback: hit Razorpay API
        payment_data = await self._fetch_payment_from_provider(pid, command_id)
        payment_status = payment_data.get("status")

        if payment_status == "captured":
            _dur = int((time.perf_counter() - _verify_start) * 1000)
            pel.verify_completed(
                command_id=command_id,
                verify_path="provider_fallback",
                client_id=user_id,
                duration_ms=_dur,
                razorpay_payment_id=pid,
                razorpay_subscription_id=sid,
            )
            pel.payment_captured(
                command_id=command_id,
                client_id=user_id,
                razorpay_payment_id=pid,
                razorpay_subscription_id=sid,
            )
            with self._session_scope() as session:
                result = await legacy_rzp.handle_captured_payment_secure(
                    session, pid, sid, payment_data
                )
                await self._run_verify_side_effects(
                    session_scope=False,
                    sid=sid,
                    user_id=user_id,
                    command_id=command_id,
                    session=session,
                )
                return result

        if payment_status == "authorized":
            pel.payment_authorized(
                command_id=command_id,
                client_id=user_id,
                razorpay_payment_id=pid,
                razorpay_subscription_id=sid,
                duration_ms=int((time.perf_counter() - _verify_start) * 1000),
            )
            return {
                "verified": True,
                "captured": False,
                "retryAfterMs": 2000,
                "message": "Payment authorized, finalizing...",
            }

        if payment_status in ["failed", "refunded"]:
            pel.verify_failed(
                command_id=command_id,
                client_id=user_id,
                error_code=payment_status,
                razorpay_payment_id=pid,
                razorpay_subscription_id=sid,
                duration_ms=int((time.perf_counter() - _verify_start) * 1000),
            )
            return {
                "verified": False,
                "captured": False,
                "status": payment_status,
                "message": f"Payment {payment_status}",
            }

        pel.verify_pending(
            command_id=command_id,
            client_id=user_id,
            razorpay_payment_id=pid,
            razorpay_subscription_id=sid,
            provider_status=payment_status,
            duration_ms=int((time.perf_counter() - _verify_start) * 1000),
        )
        return {
            "verified": True,
            "captured": False,
            "retryAfterMs": 3000,
            "message": "Payment verification in progress",
        }

    # ── Webhook ───────────────────────────────────────────────────────

    async def _persist_webhook(self, body: Dict, command_id: str) -> None:
        raw = body.get("raw_body")
        signature = body.get("signature")
        if raw is None or signature is None:
            pel.webhook_signature_invalid(command_id=command_id)
            raise ValueError("webhook_signature_missing")
        raw_bytes = raw if isinstance(raw, bytes) else raw.encode("utf-8")

        # 1. Persist payment/subscription via legacy handler
        with self._session_scope() as session:
            await process_razorpay_webhook_payload(raw_bytes, signature, session)

        # 2. Set capture marker so verify can finish instantly
        await self._record_capture_marker(body)

        # 3. Run ALL side effects (nutrition, reward, referral) — fully independent of verify
        if body.get("event") == "payment.captured":
            await self._run_webhook_side_effects(body, command_id)

    # ── Local polling ─────────────────────────────────────────────────

    async def _poll_local_confirmation(
        self, command: RpSubscriptionVerifyCommand
    ) -> Optional[Dict[str, Any]]:
        pid = command.razorpay_payment_id
        sid = command.razorpay_subscription_id
        user_id = command.user_id
        delay = max(0.2, self.config.verify_db_poll_base_delay_ms / 1000)
        max_delay = self.config.verify_db_poll_max_delay_ms / 1000
        deadline = time.monotonic() + max(1, self.config.verify_db_poll_total_timeout_seconds)

        attempt = 0
        max_attempts = max(1, self.config.verify_db_poll_attempts)
        while time.monotonic() < deadline and attempt < max_attempts:
            attempt += 1

            capture_snapshot = await self._capture_marker_snapshot(pid)
            if capture_snapshot:
                logger.info(
                    "RP_SUB_VERIFY_CAPTURE_CACHE_HIT pid=%s attempt=%s",
                    legacy_rzp._mask(pid),
                    attempt,
                )
                with self._session_scope() as session:
                    return await legacy_rzp.handle_captured_payment_secure(
                        session, pid, sid, capture_snapshot
                    )

            with self._session_scope() as session:
                # Check subscription + payment in DB
                premium_snapshot = self._premium_confirmation_snapshot(
                    session, user_id, sid, pid
                )
                if premium_snapshot:
                    logger.info(
                        "RP_SUB_VERIFY_HAS_PREMIUM pid=%s attempt=%s",
                        legacy_rzp._mask(pid),
                        attempt,
                    )
                    return await legacy_rzp.handle_captured_payment_secure(
                        session, pid, sid, premium_snapshot
                    )

                payment_data = self._payment_payload_from_db(session, pid)
                if payment_data:
                    logger.info(
                        "RP_SUB_VERIFY_WEBHOOK_PAYMENT_FOUND pid=%s attempt=%s",
                        legacy_rzp._mask(pid),
                        attempt,
                    )
                    return await legacy_rzp.handle_captured_payment_secure(
                        session, pid, sid, payment_data
                    )

            if time.monotonic() >= deadline:
                break
            await asyncio.sleep(delay + random.uniform(0, 0.3))
            delay = min(delay * 1.5, max_delay)

        return None

    def _premium_confirmation_snapshot(
        self, session: Session, user_id: Optional[str], sid: Optional[str], pid: str
    ) -> Optional[Dict[str, Any]]:
        now = legacy_rzp.now_ist()
        subscription = None
        if user_id:
            subscription = (
                session.query(Subscription)
                .filter(
                    Subscription.customer_id == user_id,
                    Subscription.provider == legacy_rzp.PROVIDER,
                    Subscription.status.in_(["active", "renewed", "pending"]),
                    or_(Subscription.active_from == None, Subscription.active_from <= now),
                    or_(Subscription.active_until == None, Subscription.active_until >= now),
                )
                .order_by(Subscription.created_at.desc())
                .first()
            )
        if not subscription and sid:
            subscription = (
                session.query(Subscription)
                .filter(
                    Subscription.provider == legacy_rzp.PROVIDER,
                    Subscription.id == sid,
                )
                .order_by(Subscription.created_at.desc())
                .first()
            )
        if not subscription or subscription.latest_txn_id != pid:
            return None
        return self._payment_payload_from_db(session, pid)

    def _payment_payload_from_db(
        self, session: Session, pid: str
    ) -> Optional[Dict[str, Any]]:
        payment = (
            session.query(Payment)
            .filter(
                Payment.provider == legacy_rzp.PROVIDER,
                Payment.provider_payment_id == pid,
                Payment.status == "captured",
            )
            .first()
        )
        if not payment:
            return None
        return {
            "amount": payment.amount_minor,
            "currency": payment.currency or "INR",
            "method": (payment.payment_metadata or {}).get("method")
            if payment.payment_metadata
            else None,
        }

    # ── Side effects ──────────────────────────────────────────────────

    async def _run_verify_side_effects(
        self,
        *,
        session_scope: bool,
        sid: str,
        user_id: Optional[str],
        command_id: str,
        session: Optional[Session] = None,
    ) -> None:
        """Run nutrition + reward + referral after verify."""
        # Nutrition
        try:
            if session_scope:
                with self._session_scope() as sess:
                    self.side_effects.grant_nutrition_from_session_scope(
                        sess,
                        customer_id=user_id or "",
                        subscription_id=sid,
                        provider=legacy_rzp.PROVIDER,
                        command_id=command_id,
                    )
            elif session:
                self.side_effects.grant_nutrition_from_session_scope(
                    session,
                    customer_id=user_id or "",
                    subscription_id=sid,
                    provider=legacy_rzp.PROVIDER,
                    command_id=command_id,
                )
        except Exception as exc:
            logger.warning("RP_SUB_VERIFY_NUTRITION_FAILED: %s", exc)

        # Reward + referral
        if user_id:
            await self.side_effects.add_reward_entries(
                customer_id=user_id,
                subscription_id=sid,
                command_id=command_id,
            )
            plan_name = self._resolve_plan_name_for_subscription(sid, user_id)
            if plan_name:
                await self.side_effects.credit_referrer_if_yearly(
                    customer_id=user_id,
                    subscription_id=sid,
                    plan_name=plan_name,
                    command_id=command_id,
                )

    async def _run_webhook_side_effects(self, body: Dict, command_id: str) -> None:
        """
        Extract customer/subscription from the webhook payload and run
        ALL side effects (nutrition, reward, referral) so the webhook path
        is fully self-sufficient — no dependency on verify running later.
        """
        try:
            pay_entity = body.get("payload", {}).get("payment", {}).get("entity", {})
            notes = pay_entity.get("notes", {})
            customer_id = notes.get("customer_id")
            subscription_id = pay_entity.get("subscription_id") or pay_entity.get("id")

            if not customer_id:
                logger.warning(
                    "RP_SUB_WEBHOOK_SIDE_EFFECTS_SKIP: no customer_id in notes",
                    extra={"command_id": command_id},
                )
                return

            # Nutrition
            try:
                with self._session_scope() as session:
                    self.side_effects.grant_nutrition_from_session_scope(
                        session,
                        customer_id=customer_id,
                        subscription_id=subscription_id or "",
                        provider=legacy_rzp.PROVIDER,
                        command_id=command_id,
                    )
            except Exception as exc:
                logger.warning("RP_SUB_WEBHOOK_NUTRITION_FAILED: %s", exc)

            # Reward entries
            await self.side_effects.add_reward_entries(
                customer_id=customer_id,
                subscription_id=subscription_id or "",
                command_id=command_id,
            )

            # Referral credit
            plan_name = self._resolve_plan_name_for_subscription(
                subscription_id or "", customer_id
            )
            if plan_name:
                await self.side_effects.credit_referrer_if_yearly(
                    customer_id=customer_id,
                    subscription_id=subscription_id or "",
                    plan_name=plan_name,
                    command_id=command_id,
                )

        except Exception as exc:
            # Best-effort — never fail the webhook
            logger.exception(
                "RP_SUB_WEBHOOK_SIDE_EFFECTS_ERROR",
                extra={"command_id": command_id, "error": str(exc)},
            )

    def _resolve_plan_name_for_subscription(
        self, subscription_id: str, user_id: Optional[str]
    ) -> Optional[str]:
        with self._session_scope() as session:
            sub = (
                session.query(Subscription)
                .filter(
                    Subscription.provider == legacy_rzp.PROVIDER,
                    Subscription.id == subscription_id,
                )
                .order_by(Subscription.created_at.desc())
                .first()
            )
            if sub and sub.product_id:
                return sub.product_id

            order = (
                session.query(Order)
                .filter(
                    Order.provider == legacy_rzp.PROVIDER,
                    Order.provider_order_id == subscription_id,
                )
                .order_by(Order.created_at.desc())
                .first()
            )
            if order:
                order_item = (
                    session.query(OrderItem)
                    .filter(OrderItem.order_id == order.id)
                    .order_by(OrderItem.id.desc())
                    .first()
                )
                if order_item and getattr(order_item, "sku", None):
                    return order_item.sku
            return None

    # ── Redis capture marker ──────────────────────────────────────────

    async def _capture_marker_snapshot(self, pid: str) -> Optional[Dict[str, Any]]:
        if not self.redis:
            return None
        key = f"{self.config.rp_subscription_redis_prefix}:capture:{pid}"
        raw = await asyncio.to_thread(self.redis.get, key)
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None
        amount = payload.get("amount")
        if amount is None:
            return None
        return {
            "amount": int(amount),
            "currency": payload.get("currency") or "INR",
            "method": payload.get("method"),
        }

    async def _record_capture_marker(self, body: Dict) -> None:
        if not self.redis:
            return
        event = body.get("event")
        if event != "payment.captured":
            return
        pay_entity = body.get("payload", {}).get("payment", {}).get("entity", {})
        payment_id = pay_entity.get("id")
        if not payment_id:
            return
        marker = {
            "subscription_id": pay_entity.get("subscription_id"),
            "amount": pay_entity.get("amount"),
            "currency": pay_entity.get("currency"),
            "method": pay_entity.get("method"),
            "customer_id": pay_entity.get("notes", {}).get("customer_id"),
            "order_id": pay_entity.get("order_id"),
            "captured_at": pay_entity.get("created_at") or int(time.time()),
        }
        key = f"{self.config.rp_subscription_redis_prefix}:capture:{payment_id}"
        try:
            await asyncio.to_thread(
                self.redis.set,
                key,
                json.dumps(marker),
                ex=self.config.verify_capture_cache_ttl_seconds,
            )
            logger.info(
                "RP_SUB_CAPTURE_CACHE_SET pid=%s sub=%s",
                legacy_rzp._mask(payment_id),
                marker.get("subscription_id"),
            )
        except Exception:
            logger.exception("Failed to set RP subscription capture cache")

    # ── Helpers ────────────────────────────────────────────────────────

    async def _fetch_catalog(self, session: Session, plan_sku: str) -> CatalogProduct:
        def _op() -> CatalogProduct:
            return (
                session.query(CatalogProduct)
                .filter(CatalogProduct.sku == plan_sku, CatalogProduct.active.is_(True))
                .first()
            )
        return await run_sync_db_operation(_op)

    async def _maybe_fetch_plan(self, plan_id: str) -> Dict[str, Any]:
        try:
            return await self._provider_call(rzp_get_plan(plan_id))
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            logger.warning("Plan validation skipped: %s", exc)
            return {}

    async def _provider_call(self, coro):
        async with self._provider_semaphore:
            return await asyncio.wait_for(coro, timeout=self.config.provider_timeout_seconds)

    @staticmethod
    def _resolve_total_count(plan_entity: Dict[str, Any]) -> int:
        period = (plan_entity or {}).get("period")
        interval = (plan_entity or {}).get("interval")
        if period in ("year", "yearly"):
            return 1
        if period == "monthly" and interval and int(interval) > 1:
            return 1
        return 12

    async def _fetch_payment_from_provider(
        self, pid: str, command_id: str
    ) -> Dict[str, Any]:
        attempts = max(1, self.config.verify_provider_max_attempts)
        last_exc: Optional[Exception] = None
        for attempt in range(1, attempts + 1):
            _call_start = time.perf_counter()
            try:
                pel.provider_call_started(
                    command_id=command_id,
                    provider_endpoint="get_payment",
                    attempt=attempt,
                )
                result = await self._provider_call(rzp_get_payment(pid))
                pel.provider_call_completed(
                    command_id=command_id,
                    provider_endpoint="get_payment",
                    duration_ms=int((time.perf_counter() - _call_start) * 1000),
                )
                return result
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                pel.provider_call_failed(
                    command_id=command_id,
                    provider_endpoint="get_payment",
                    error_code=type(exc).__name__,
                    duration_ms=int((time.perf_counter() - _call_start) * 1000),
                    attempt=attempt,
                )
                last_exc = exc
                await asyncio.sleep(min(attempt * 1.5, 5))
        if last_exc:
            raise last_exc
        return {}

    @contextmanager
    def _session_scope(self):
        with self.payment_db.get_session() as session:
            yield session
