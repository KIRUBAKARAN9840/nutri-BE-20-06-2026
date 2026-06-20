"""Database & Redis queries for client login and registration.

All raw DB/Redis access lives here. No business logic.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.exc import IntegrityError

from app.models.fittbot_models import (
    AdRegistration,
    AuthEvent,
    Client,
    ClientTarget,
    Gym,
    ReferralCode,
    ReferralFittbotCash,
    ReferralMapping,
)
from app.fittbot_api.v1.payments.models.credits import CreditBalance, CreditLedger

OTP_TTL_SECONDS = 300  # 5 minutes


class LoginRepository:
    """Encapsulates all DB + Redis queries for the login/registration flow."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis

    # -- Client lookup / create ------------------------------------------------

    async def get_client_by_mobile(self, mobile: str) -> Optional[Client]:
        stmt = select(Client).where(Client.contact == mobile)
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def get_client_by_id(self, client_id: int) -> Optional[Client]:
        stmt = select(Client).where(Client.client_id == client_id)
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def create_stub_client(self, mobile: str) -> Client:

        client = Client(
            name="",
            contact=mobile,
            gender="",
            email="",
            password="",
            verification='{"mobile": true, "password": false}',
            profile="",
            access=False,
            incomplete=True,
            modal_shown=True,
        )
        self.db.add(client)
        await self.db.flush()
        return client

    # -- Gym -------------------------------------------------------------------

    async def get_gym(self, gym_id: int) -> Optional[Gym]:
        stmt = select(Gym).where(Gym.gym_id == gym_id)
        result = await self.db.execute(stmt)
        return result.scalars().first()

    # -- OTP (Redis) -----------------------------------------------------------

    async def store_otp(self, mobile: str, otp: str) -> None:
        await self.redis.set(f"otp:{mobile}", otp, ex=OTP_TTL_SECONDS)

    async def get_and_delete_otp(self, mobile: str) -> Optional[str]:
        """Atomic get+delete. Only one caller gets the value -- prevents
        double-verify race condition."""
        return await self.redis.getdel(f"otp:{mobile}")

    async def get_otp(self, mobile: str) -> Optional[str]:
        """Read OTP without deleting it (for retry flow)."""
        val = await self.redis.get(f"otp:{mobile}")
        return val.decode() if isinstance(val, bytes) else val

    async def delete_otp(self, mobile: str) -> None:
        await self.redis.delete(f"otp:{mobile}")

    async def increment_otp_attempts(self, mobile: str) -> int:
        """Increment and return the failed-attempt count. TTL matches OTP."""
        key = f"otp_attempts:{mobile}"
        count = await self.redis.incr(key)
        if count == 1:
            await self.redis.expire(key, OTP_TTL_SECONDS)
        return count

    async def clear_otp_attempts(self, mobile: str) -> None:
        await self.redis.delete(f"otp_attempts:{mobile}")

    async def otp_exists(self, mobile: str) -> bool:
        """Check if an OTP is already pending for this number."""
        return await self.redis.exists(f"otp:{mobile}") == 1

    # -- Referral --------------------------------------------------------------

    async def get_referral_by_client(self, client_id: int) -> Optional[ReferralCode]:
        stmt = select(ReferralCode).where(ReferralCode.client_id == client_id)
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def get_referral_by_code(self, code: str) -> Optional[ReferralCode]:
        stmt = select(ReferralCode).where(ReferralCode.referral_code == code)
        result = await self.db.execute(stmt)
        return result.scalars().first()

    async def create_referral_code(self, client_id: int, code: str) -> ReferralCode:
        entry = ReferralCode(
            client_id=client_id,
            referral_code=code,
            created_at=datetime.now(),
        )
        self.db.add(entry)
        await self.db.flush()
        return entry

    async def create_referral_mapping(
        self, referrer_id: int, referee_id: int
    ) -> None:
        mapping = ReferralMapping(
            referrer_id=referrer_id,
            referee_id=referee_id,
            referral_date=date.today(),
            status="completed",
        )
        self.db.add(mapping)

    async def add_fittbot_cash(self, client_id: int, amount: int) -> None:
        stmt = select(ReferralFittbotCash).where(
            ReferralFittbotCash.client_id == client_id
        )
        result = await self.db.execute(stmt)
        existing = result.scalars().first()

        if existing:
            existing.fittbot_cash += amount
        else:
            self.db.add(
                ReferralFittbotCash(client_id=client_id, fittbot_cash=amount)
            )

    # -- Client Target ---------------------------------------------------------

    async def create_client_target(self, client_id: int, defaults: dict) -> ClientTarget:
        target = ClientTarget(client_id=client_id, **defaults)
        self.db.add(target)
        return target

    # -- Ad Registration -------------------------------------------------------

    async def create_ad_registration(self, client_id: int) -> AdRegistration:
        """Record that this client signed up via an ad funnel."""
        row = AdRegistration(client_id=client_id)
        self.db.add(row)
        return row

    # -- Audit trail -----------------------------------------------------------

    async def log_auth_event(
        self,
        event_type: str,
        *,
        mobile: str | None = None,
        client_id: int | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        status: str = "success",
        detail: str | None = None,
    ) -> None:
        """Write a row to auth_events. Fire-and-forget, never fails the request."""
        try:
            event = AuthEvent(
                client_id=client_id,
                mobile=mobile,
                event_type=event_type,
                ip_address=ip_address,
                user_agent=user_agent,
                status=status,
                detail=detail,
            )
            self.db.add(event)
            # flushed with the next commit -- no extra round trip
        except Exception:
            pass  # audit must never break auth flow

    # -- Signup Credits --------------------------------------------------------

    async def grant_signup_credits(
        self, client_id: int, credits: int, expiry_days: int
    ) -> None:
        """Write 5 free credits to credit_balances + credit_ledger.
        Standalone session so it doesn't interfere with the main registration tx."""
        import uuid
        from app.models.async_database import get_async_sessionmaker

        async_session = get_async_sessionmaker()
        async with async_session() as session:
            try:
                dedup_key = f"signup_credits_{client_id}"

                # Check duplicate
                stmt = select(CreditLedger).where(
                    CreditLedger.source_order_id == dedup_key,
                    CreditLedger.txn_type == "signup_bonus",
                )
                result = await session.execute(stmt)
                if result.scalars().first():
                    return  # already granted

                # Upsert credit_balances
                stmt = select(CreditBalance).where(
                    CreditBalance.client_id == client_id
                )
                result = await session.execute(stmt)
                balance_row = result.scalars().first()

                if not balance_row:
                    balance_row = CreditBalance(
                        client_id=client_id,
                        balance=0, total_purchased=0, total_bonus=0, total_used=0,
                    )
                    session.add(balance_row)
                    await session.flush()

                balance_row.balance += credits
                balance_row.total_bonus += credits
                new_balance = balance_row.balance

                # Append ledger entry
                from zoneinfo import ZoneInfo
                ist_now = datetime.now(ZoneInfo("Asia/Kolkata"))
                unique_id = f"crl_{int(ist_now.timestamp())}_{str(uuid.uuid4())[:8]}"
                expires_at = ist_now + timedelta(days=expiry_days)

                ledger = CreditLedger(
                    id=unique_id,
                    client_id=client_id,
                    txn_type="signup_bonus",
                    credits=credits,
                    balance_after=new_balance,
                    source_order_id=dedup_key,
                    description=f"Welcome bonus ({credits} free credits, expires in {expiry_days}d)",
                    expires_at=expires_at,
                    created_at=ist_now,
                )
                session.add(ledger)
                await session.commit()

                # Invalidate home cache
                try:
                    keys = await self.redis.keys(f"home:data:{client_id}:*")
                    ustate_keys = await self.redis.keys(f"home:v2:ustate:{client_id}")
                    all_keys = (keys or []) + (ustate_keys or [])
                    if all_keys:
                        await self.redis.delete(*all_keys)
                except Exception:
                    pass

            except IntegrityError:
                await session.rollback()  # duplicate, safe to ignore
            except Exception:
                await session.rollback()
                raise

    # -- Transaction helpers ---------------------------------------------------

    async def commit(self) -> None:
        await self.db.commit()

    async def rollback(self) -> None:
        await self.db.rollback()

    async def refresh(self, obj) -> None:
        await self.db.refresh(obj)
