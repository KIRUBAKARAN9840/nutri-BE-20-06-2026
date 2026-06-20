"""
Shared subscription side effects: nutrition eligibility, rewards, referral credits,
and subscription/trial credit grants.

Both Google Play and Razorpay processors delegate to this service so
the business logic lives in one place.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from ..._deps.models import Order, OrderItem, Subscription, CatalogProduct, ItemType
from ..._deps.event_logger import PaymentEventLogger
from ..._deps.utils import now_ist
from app.fittbot_api.v1.client.client_api.nutrition.nutrition_eligibility_service import (
    grant_nutrition_eligibility_sync,
    calculate_nutrition_sessions_from_fittbot_plan,
)
from app.fittbot_api.v1.client.client_api.reward_program.reward_service import (
    add_subscription_entry,
)
from app.models.async_database import create_celery_async_sessionmaker
from app.fittbot_api.v1.payments.Fittbot_Subscriptions_concurrent.services.referral_cash_service import (
    maybe_credit_referrer_for_yearly_subscription,
)

logger = logging.getLogger("payments.v2.subscriptions.side_effects")


class SubscriptionSideEffects:
    """
    Encapsulates all best-effort side effects triggered after a
    subscription is verified or a webhook is processed.
    """

    def __init__(self, pel: PaymentEventLogger):
        self.pel = pel

    # ── Nutrition ──────────────────────────────────────────────────────

    def grant_nutrition_if_eligible(
        self,
        session: Session,
        *,
        customer_id: str,
        subscription_id: str,
        product_id: str,
        command_id: str,
    ) -> None:
        plan_name = product_id.lower() if product_id else ""
        duration_months = self._resolve_duration_months(plan_name)

        if duration_months < 6:
            self.pel.side_effect_skipped(
                command_id=command_id,
                side_effect="nutrition",
                reason="duration_lt_6m",
                client_id=customer_id,
            )
            return

        sessions = calculate_nutrition_sessions_from_fittbot_plan(plan_name, duration_months)
        if sessions <= 0:
            self.pel.side_effect_skipped(
                command_id=command_id,
                side_effect="nutrition",
                reason="sessions_zero",
                client_id=customer_id,
            )
            return

        grant_nutrition_eligibility_sync(
            db=session,
            client_id=int(customer_id),
            source_type="fittbot_subscription",
            source_id=subscription_id,
            plan_name=product_id,
            duration_months=duration_months,
            gym_id=None,
        )
        session.commit()
        self.pel.side_effect_success(
            command_id=command_id, side_effect="nutrition", client_id=customer_id
        )
        logger.info(
            "[NUTRITION_ELIGIBILITY_V2] client_id=%s sub_id=%s plan=%s duration=%sm sessions=%s",
            customer_id, subscription_id, product_id, duration_months, sessions,
        )

    def grant_nutrition_from_session_scope(
        self,
        session: Session,
        *,
        customer_id: str,
        subscription_id: str,
        provider: str,
        command_id: str,
    ) -> None:
        """Resolve product_id from DB, then grant nutrition. Used in verify paths."""
        product_id = self._resolve_product_id(session, subscription_id, customer_id, provider)
        if not product_id:
            return
        try:
            self.grant_nutrition_if_eligible(
                session,
                customer_id=customer_id,
                subscription_id=subscription_id,
                product_id=product_id,
                command_id=command_id,
            )
        except Exception as exc:
            self.pel.side_effect_failed(
                command_id=command_id, side_effect="nutrition",
                error_detail=str(exc), client_id=customer_id,
            )
            logger.warning("NUTRITION_ELIGIBILITY_FAILED sub=%s err=%s", subscription_id, exc)

    # ── Reward entries ─────────────────────────────────────────────────

    async def add_reward_entries(
        self,
        *,
        customer_id: str,
        subscription_id: str,
        command_id: str,
    ) -> None:
        if not customer_id:
            logger.warning("[REWARD_ENTRY_SKIPPED] No customer_id for sub=%s", subscription_id)
            return
        try:
            async_session_maker = create_celery_async_sessionmaker()
            async with async_session_maker() as async_db:
                reward_ok, entries_added, reward_msg = await add_subscription_entry(
                    async_db,
                    client_id=int(customer_id),
                    source_id=subscription_id,
                )
                await async_db.commit()
            if reward_ok:
                self.pel.side_effect_success(
                    command_id=command_id, side_effect="reward", client_id=customer_id
                )
                logger.info(
                    "SUBSCRIPTION_REWARD_ENTRY_ADDED client=%s sub=%s entries=%s",
                    customer_id, subscription_id, entries_added,
                )
            else:
                logger.warning("REWARD_ENTRY_SKIPPED msg=%s", reward_msg)
        except Exception as exc:
            self.pel.side_effect_failed(
                command_id=command_id, side_effect="reward",
                error_detail=str(exc), client_id=customer_id,
            )
            logger.warning("REWARD_ENTRY_FAILED sub=%s err=%s", subscription_id, exc)

    # ── Referral credit ────────────────────────────────────────────────

    async def credit_referrer_if_yearly(
        self,
        *,
        customer_id: str,
        subscription_id: str,
        plan_name: str,
        command_id: str,
    ) -> None:
        try:
            await maybe_credit_referrer_for_yearly_subscription(
                referee_id=customer_id,
                subscription_id=subscription_id,
                plan_name=plan_name,
            )
            self.pel.side_effect_success(
                command_id=command_id, side_effect="referral", client_id=customer_id
            )
        except Exception as exc:
            self.pel.side_effect_failed(
                command_id=command_id, side_effect="referral",
                error_detail=str(exc), client_id=customer_id,
            )
            logger.warning("REFERRAL_CREDIT_FAILED sub=%s err=%s", subscription_id, exc)

    # ── Credit grants (trial + subscription) ─────────────────────────

    TRIAL_CREDITS = 5
    TRIAL_CREDIT_EXPIRY_DAYS = 3
    SUBSCRIPTION_CREDITS = 100
    SUBSCRIPTION_CREDIT_EXPIRY_DAYS = 30

    def grant_trial_credits(
        self,
        session: Session,
        *,
        customer_id: str,
        subscription_id: str,
        trial_end: Optional[datetime] = None,
        command_id: str,
    ) -> None:
        """Grant 5 credits with 3-day expiry for free trial."""
        from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits.credits.shared.credit_service import (
            CreditService, DuplicateGrantError,
        )
        try:
            svc = CreditService(session)
            expires_at = trial_end or (now_ist() + timedelta(days=self.TRIAL_CREDIT_EXPIRY_DAYS))
            # source_order_id is the dedup key (UNIQUE constraint in credit_ledger)
            dedup_key = f"trial_credits_{customer_id}_{subscription_id}"
            svc.grant_credits(
                client_id=int(customer_id),
                credits=self.TRIAL_CREDITS,
                txn_type="trial_bonus",
                source_order_id=dedup_key,
                source_subscription_id=subscription_id,
                description=f"Free trial bonus ({self.TRIAL_CREDITS} credits, expires in {self.TRIAL_CREDIT_EXPIRY_DAYS}d)",
                expires_at=expires_at,
            )
            session.flush()
            self.pel.side_effect_success(
                command_id=command_id, side_effect="trial_credits", client_id=customer_id
            )
            logger.info(
                "TRIAL_CREDITS_GRANTED | customer=%s credits=%d expires=%s sub=%s",
                customer_id, self.TRIAL_CREDITS, expires_at.isoformat(), subscription_id,
            )
        except DuplicateGrantError:
            logger.info("TRIAL_CREDITS_ALREADY_GRANTED | customer=%s sub=%s", customer_id, subscription_id)
        except Exception as exc:
            self.pel.side_effect_failed(
                command_id=command_id, side_effect="trial_credits",
                error_detail=str(exc), client_id=customer_id,
            )
            logger.warning("TRIAL_CREDITS_FAILED | customer=%s err=%s", customer_id, exc)

    def grant_subscription_credits(
        self,
        session: Session,
        *,
        customer_id: str,
        subscription_id: str,
        transaction_id: str = "",
        command_id: str,
    ) -> None:
        """Grant 100 credits with 30-day expiry for paid subscription."""
        from app.fittbot_api.v2.Fymble_Payments.Subscriptions_AiCredits.credits.shared.credit_service import (
            CreditService, DuplicateGrantError,
        )
        try:
            svc = CreditService(session)
            expires_at = now_ist() + timedelta(days=self.SUBSCRIPTION_CREDIT_EXPIRY_DAYS)
            # Dedup: subscription_id + transaction_id
            # Same purchase (webhook + verify) → same key → deduped
            # Renewal → new transaction_id → new credits
            txn_part = transaction_id or subscription_id
            dedup_key = f"sub_credits_{subscription_id}_{txn_part}"
            svc.grant_credits(
                client_id=int(customer_id),
                credits=self.SUBSCRIPTION_CREDITS,
                txn_type="subscription_bonus",
                source_order_id=dedup_key,
                source_subscription_id=subscription_id,
                description=f"Subscription bonus ({self.SUBSCRIPTION_CREDITS} credits, expires in {self.SUBSCRIPTION_CREDIT_EXPIRY_DAYS}d)",
                expires_at=expires_at,
            )
            session.flush()
            self.pel.side_effect_success(
                command_id=command_id, side_effect="subscription_credits", client_id=customer_id
            )
            logger.info(
                "SUBSCRIPTION_CREDITS_GRANTED | customer=%s credits=%d expires=%s sub=%s",
                customer_id, self.SUBSCRIPTION_CREDITS, expires_at.isoformat(), subscription_id,
            )
        except DuplicateGrantError:
            logger.info("SUBSCRIPTION_CREDITS_ALREADY_GRANTED | customer=%s sub=%s", customer_id, subscription_id)
        except Exception as exc:
            self.pel.side_effect_failed(
                command_id=command_id, side_effect="subscription_credits",
                error_detail=str(exc), client_id=customer_id,
            )
            logger.warning("SUBSCRIPTION_CREDITS_FAILED | customer=%s err=%s", customer_id, exc)

    # ── Catalog helpers ───────────────────────────────────────────────

    @staticmethod
    def ensure_nutrition_catalog(session: Session) -> None:
        """Ensure the nutrition SKU exists in catalog_products."""
        sku = "fymble_nutrition:one-month-fymble-nutrition"
        existing = session.query(CatalogProduct).filter(CatalogProduct.sku == sku).first()
        if not existing:
            session.add(CatalogProduct(
                sku=sku,
                item_type=ItemType.app_subscription.value,
                title="Fymble Nutrition - 1 Month",
                base_amount_minor=99900,
                description="One month Fymble nutrition plan",
                active=True,
            ))
            session.flush()
            logger.info("CATALOG_PRODUCT_CREATED | sku=%s", sku)

    # ── Convenience: run all side effects ──────────────────────────────

    async def run_all_trial(
        self,
        session: Session,
        *,
        customer_id: str,
        subscription_id: str,
        trial_end: Optional[datetime] = None,
        command_id: str,
    ) -> None:
        """Run trial-specific side effects: grant 5 credits only. Best-effort."""
        self.grant_trial_credits(
            session,
            customer_id=customer_id,
            subscription_id=subscription_id,
            trial_end=trial_end,
            command_id=command_id,
        )

    async def run_all_paid(
        self,
        session: Session,
        *,
        customer_id: str,
        subscription_id: str,
        product_id: str,
        command_id: str,
        is_new_subscription: bool = True,
    ) -> None:
        """Run paid-subscription side effects: 100 credits + nutrition + reward + referral. Best-effort."""
        # Grant 100 subscription credits
        self.grant_subscription_credits(
            session,
            customer_id=customer_id,
            subscription_id=subscription_id,
            command_id=command_id,
        )

        try:
            self.grant_nutrition_if_eligible(
                session,
                customer_id=customer_id,
                subscription_id=subscription_id,
                product_id=product_id,
                command_id=command_id,
            )
        except Exception as exc:
            self.pel.side_effect_failed(
                command_id=command_id, side_effect="nutrition",
                error_detail=str(exc), client_id=customer_id,
            )
            logger.warning("NUTRITION_SIDE_EFFECT_FAILED: %s", exc)

        if is_new_subscription:
            await self.add_reward_entries(
                customer_id=customer_id,
                subscription_id=subscription_id,
                command_id=command_id,
            )
            await self.credit_referrer_if_yearly(
                customer_id=customer_id,
                subscription_id=subscription_id,
                plan_name=product_id,
                command_id=command_id,
            )

    async def run_all(
        self,
        session: Session,
        *,
        customer_id: str,
        subscription_id: str,
        product_id: str,
        command_id: str,
        is_new_subscription: bool = True,
    ) -> None:
        """Run nutrition + reward + referral + credits. Best-effort, never raises."""
        await self.run_all_paid(
            session,
            customer_id=customer_id,
            subscription_id=subscription_id,
            product_id=product_id,
            command_id=command_id,
            is_new_subscription=is_new_subscription,
        )

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_duration_months(plan_name: str) -> int:
        lower = plan_name.lower()
        if "half-yearly" in lower or "half_yearly" in lower:
            return 6
        if "12" in lower or "twelve" in lower or "yearly" in lower:
            return 12
        if "6" in lower or "six" in lower:
            return 6
        return 0

    @staticmethod
    def _resolve_product_id(
        session: Session,
        subscription_id: str,
        customer_id: str,
        provider: str,
    ) -> Optional[str]:
        sub = (
            session.query(Subscription)
            .filter(
                Subscription.provider == provider,
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
                Order.provider == provider,
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
