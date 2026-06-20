


import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..._deps.models import CreditBalance, CreditLedger
from ..._deps.database import generate_unique_id
from ..._deps.utils import now_ist

logger = logging.getLogger("payments.credits.service")


class InsufficientCreditsError(Exception):
    """Raised when the client does not have enough credits."""


class DuplicateGrantError(Exception):
    """Raised when the same source_order_id + txn_type was already granted."""


class CreditService:
    """Atomic credit operations — expects a Session managed by the caller."""

    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _invalidate_home_cache(client_id: int):
        """Delete all home feed cache keys for this client (any geohash)."""
        try:
            from app.utils.redis_config import get_redis_sync
            r = get_redis_sync()
            keys = r.keys(f"home:data:{client_id}:*")
            ustate_keys = r.keys(f"home:v2:ustate:{client_id}")
            all_keys = (keys or []) + (ustate_keys or [])
            if all_keys:
                r.delete(*all_keys)
        except Exception:
            pass  # non-critical — cache will expire in 5 min anyway

    # ── Public API ──────────────────────────────────────────────────

    def grant_credits(
        self,
        client_id: int,
        credits: int,
        txn_type: str,
        *,
        source_order_id: Optional[str] = None,
        source_subscription_id: Optional[str] = None,
        description: str = "",
        expires_at: Optional[datetime] = None,
    ) -> int:

        if source_order_id:
            existing = (
                self.session.query(CreditLedger)
                .filter(
                    CreditLedger.source_order_id == source_order_id,
                    CreditLedger.txn_type == txn_type,
                )
                .first()
            )
            if existing:
                logger.info(
                    "CREDIT_GRANT_DUPLICATE_SKIPPED | client=%s order=%s txn=%s",
                    client_id,
                    source_order_id,
                    txn_type,
                )
                raise DuplicateGrantError(
                    f"Already granted for order {source_order_id}"
                )

        # 2. Lock balance row (SELECT … FOR UPDATE), upsert if absent
        balance_row = self._lock_or_create_balance(client_id)

        # 3. Apply credit
        balance_row.balance += credits
        if txn_type == "purchase":
            balance_row.total_purchased += credits
        elif txn_type in ("subscription_bonus", "admin_grant", "trial_bonus", "signup_bonus"):
            balance_row.total_bonus += credits

        new_balance = balance_row.balance
        self.session.add(balance_row)

        # 4. Append ledger entry
        ledger = CreditLedger(
            id=generate_unique_id("crl"),
            client_id=client_id,
            txn_type=txn_type,
            credits=credits,
            balance_after=new_balance,
            source_order_id=source_order_id,
            source_subscription_id=source_subscription_id,
            description=description,
            expires_at=expires_at,
            created_at=now_ist(),
        )
        self.session.add(ledger)
        nested = self.session.begin_nested()

        try:
            self.session.flush()
            nested.commit()
        except IntegrityError:
            nested.rollback()
            raise DuplicateGrantError(
                f"Concurrent duplicate grant for order {source_order_id}"
            )

        logger.info(
            "CREDIT_GRANTED | client=%s credits=%+d balance=%d txn=%s order=%s",
            client_id,
            credits,
            new_balance,
            txn_type,
            source_order_id,
        )
        self._invalidate_home_cache(client_id)
        return new_balance

    # ── Unlimited scan pass (credit_999) ────────────────────────────

    def is_unlimited_active(self, client_id: int) -> bool:
        """True if the client has an unexpired unlimited-scan pass.

        Comparison is done DB-side so we never trip over naive/aware
        datetime mismatches. Both stored value and now_ist() are IST
        wall-clock, so the comparison is consistent.
        """
        return (
            self.session.query(CreditBalance.client_id)
            .filter(
                CreditBalance.client_id == client_id,
                CreditBalance.unlimited_until.isnot(None),
                CreditBalance.unlimited_until > now_ist(),
            )
            .first()
            is not None
        )

    def grant_scan_pass(
        self,
        client_id: int,
        *,
        validity: timedelta,
        source_order_id: Optional[str] = None,
        source_subscription_id: Optional[str] = None,
        description: str = "Unlimited scan pass",
    ) -> datetime:
        """Activate / extend an unlimited-scan pass. Returns new `unlimited_until`.

        Idempotent on (source_order_id, "scan_pass"). If a pass is already
        active, the new validity STACKS on top of the remaining time.
        """
        if source_order_id:
            existing = (
                self.session.query(CreditLedger)
                .filter(
                    CreditLedger.source_order_id == source_order_id,
                    CreditLedger.txn_type == "scan_pass",
                )
                .first()
            )
            if existing:
                logger.info(
                    "SCAN_PASS_DUPLICATE_SKIPPED | client=%s order=%s",
                    client_id, source_order_id,
                )
                raise DuplicateGrantError(
                    f"Scan pass already granted for order {source_order_id}"
                )

        balance_row = self._lock_or_create_balance(client_id)

        now = now_ist()
        current = balance_row.unlimited_until
        base = current if (current and current > now) else now
        new_until = base + validity
        balance_row.unlimited_until = new_until
        self.session.add(balance_row)

        ledger = CreditLedger(
            id=generate_unique_id("crl"),
            client_id=client_id,
            txn_type="scan_pass",
            credits=0,  # a pass moves no credit balance
            balance_after=balance_row.balance,
            source_order_id=source_order_id,
            source_subscription_id=source_subscription_id,
            description=description,
            expires_at=new_until,
            created_at=now,
        )
        self.session.add(ledger)
        nested = self.session.begin_nested()
        try:
            self.session.flush()
            nested.commit()
        except IntegrityError:
            nested.rollback()
            raise DuplicateGrantError(
                f"Concurrent duplicate scan pass for order {source_order_id}"
            )

        logger.info(
            "SCAN_PASS_GRANTED | client=%s until=%s order=%s",
            client_id, new_until.isoformat(), source_order_id,
        )
        self._invalidate_home_cache(client_id)
        return new_until

    def revoke_scan_pass(
        self,
        client_id: int,
        *,
        description: str = "Unlimited scan pass refunded",
    ) -> None:
        """Deactivate an unlimited-scan pass (e.g. on refund)."""
        balance_row = self._lock_or_create_balance(client_id)
        if balance_row.unlimited_until is None:
            return
        balance_row.unlimited_until = None
        self.session.add(balance_row)

        ledger = CreditLedger(
            id=generate_unique_id("crl"),
            client_id=client_id,
            txn_type="scan_pass_refund",
            credits=0,
            balance_after=balance_row.balance,
            description=description,
            created_at=now_ist(),
        )
        self.session.add(ledger)
        self.session.flush()
        self._invalidate_home_cache(client_id)

    def deduct_credit(
        self,
        client_id: int,
        amount: int = 1,
        *,
        description: str = "Food scan",
    ) -> int:
        # First expire any stale credits
        self.expire_stale_credits(client_id)

        balance_row = self._lock_or_create_balance(client_id)

        if balance_row.balance < amount:
            raise InsufficientCreditsError(
                f"Need {amount} credits, only {balance_row.balance} available"
            )

        balance_row.balance -= amount
        balance_row.total_used += amount
        new_balance = balance_row.balance
        self.session.add(balance_row)

        ledger = CreditLedger(
            id=generate_unique_id("crl"),
            client_id=client_id,
            txn_type="used",
            credits=-amount,
            balance_after=new_balance,
            description=description,
            created_at=now_ist(),
        )
        self.session.add(ledger)
        self.session.flush()

        self._invalidate_home_cache(client_id)
        return new_balance

    def expire_stale_credits(self, client_id: int) -> int:
        """
        Find all grant ledger entries with expires_at in the past that
        still have remaining credits. Deduct expired credits from balance.
        Returns total credits expired.
        """
        now = now_ist()
        balance_row = self._lock_or_create_balance(client_id)

        # Find all expirable grants (positive credits with an expires_at)
        expirable_grants = (
            self.session.query(CreditLedger)
            .filter(
                CreditLedger.client_id == client_id,
                CreditLedger.txn_type.in_(["trial_bonus", "subscription_bonus", "signup_bonus"]),
                CreditLedger.credits > 0,
                CreditLedger.expires_at.isnot(None),
                CreditLedger.expires_at < now,
            )
            .all()
        )

        if not expirable_grants:
            return 0

        # Check which grants have already been expired (by matching source)
        already_expired_sources = set()
        expired_ledgers = (
            self.session.query(CreditLedger.source_subscription_id)
            .filter(
                CreditLedger.client_id == client_id,
                CreditLedger.txn_type == "expired",
                CreditLedger.source_subscription_id.isnot(None),
            )
            .all()
        )
        for (src_id,) in expired_ledgers:
            already_expired_sources.add(src_id)

        total_expired = 0
        for grant in expirable_grants:
            grant_source = grant.source_subscription_id or grant.id
            if grant_source in already_expired_sources:
                continue

            # Calculate how many credits from this grant are still active
            # (grant amount minus any usage that can be attributed)
            credits_to_expire = grant.credits  # expire the full grant amount
            if credits_to_expire <= 0:
                continue

            # Don't expire more than current balance
            credits_to_expire = min(credits_to_expire, balance_row.balance)
            if credits_to_expire <= 0:
                continue

            balance_row.balance -= credits_to_expire
            total_expired += credits_to_expire

            expire_ledger = CreditLedger(
                id=generate_unique_id("crl"),
                client_id=client_id,
                txn_type="expired",
                credits=-credits_to_expire,
                balance_after=balance_row.balance,
                source_subscription_id=grant_source,
                description=f"Expired {grant.txn_type} credits",
                created_at=now,
            )
            self.session.add(expire_ledger)

        if total_expired > 0:
            self.session.add(balance_row)
            self.session.flush()
            logger.info(
                "CREDITS_EXPIRED | client=%s expired=%d new_balance=%d",
                client_id, total_expired, balance_row.balance,
            )

        return total_expired

    def get_balance(self, client_id: int) -> CreditBalance:
        """Return the balance row (no lock). Expires stale credits first."""
        row = (
            self.session.query(CreditBalance)
            .filter(CreditBalance.client_id == client_id)
            .first()
        )
        if not row:
            row = CreditBalance(
                client_id=client_id,
                balance=0,
                total_purchased=0,
                total_bonus=0,
                total_used=0,
            )
            self.session.add(row)
            self.session.flush()
            return row
        # Expire stale credits before returning balance
        self.expire_stale_credits(client_id)
        self.session.refresh(row)
        return row

    def get_history(
        self, client_id: int, *, limit: int = 20, offset: int = 0
    ):
        """Return (entries, total_count) for the ledger."""
        base = self.session.query(CreditLedger).filter(
            CreditLedger.client_id == client_id
        )
        total = base.count()
        entries = (
            base.order_by(CreditLedger.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return entries, total

    # ── Private helpers ─────────────────────────────────────────────

    def _lock_or_create_balance(self, client_id: int) -> CreditBalance:
        """SELECT … FOR UPDATE; create row on first access."""
        row = (
            self.session.query(CreditBalance)
            .filter(CreditBalance.client_id == client_id)
            .with_for_update()
            .first()
        )
        if row is None:
            row = CreditBalance(
                client_id=client_id,
                balance=0,
                total_purchased=0,
                total_bonus=0,
                total_used=0,
            )
            self.session.add(row)
            self.session.flush()
            # Re-lock the freshly-inserted row
            row = (
                self.session.query(CreditBalance)
                .filter(CreditBalance.client_id == client_id)
                .with_for_update()
                .one()
            )
        return row
