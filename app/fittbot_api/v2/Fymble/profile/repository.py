"""Async data-access layer for the v2 Profile module.

All SQLAlchemy queries (async `select`) and Redis key patterns used by the
contact-change OTP flow live here. The service layer never touches
`session.execute` or `redis.set` directly.
"""

from typing import Optional

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fittbot_models import Client, ClientTarget


# ── Redis key namespace for the contact-change flow ──────────────────
# Dedicated namespace so it cannot collide with auth `otp:{phone}` keys
# (password reset, signup, etc.).

PENDING_TTL = 300  # 5 minutes — matches OTP_EXPIRY_SECONDS


def _pending_contact_key(client_id: int) -> str:
    return f"profile:pending_contact:{client_id}"


def _old_otp_key(client_id: int) -> str:
    return f"profile:contact_otp:old:{client_id}"


def _new_otp_key(client_id: int) -> str:
    return f"profile:contact_otp:new:{client_id}"


def old_lockout_identifier(client_id: int) -> str:
    """Identifier passed to secure_verify_otp for the OLD-number OTP."""
    return f"profile:contact:{client_id}:old"


def new_lockout_identifier(client_id: int) -> str:
    """Identifier passed to secure_verify_otp for the NEW-number OTP."""
    return f"profile:contact:{client_id}:new"


class ProfileRepository:
    """Async DB + Redis access for profile reads, edits, and contact change."""

    def __init__(self, db: AsyncSession, redis: Redis):
        self.db = db
        self.redis = redis

    # ── Client lookups ───────────────────────────────────────────────

    async def get_client(self, client_id: int) -> Optional[Client]:
        result = await self.db.execute(
            select(Client).where(Client.client_id == client_id)
        )
        return result.scalar_one_or_none()

    async def is_contact_taken_by_other(
        self, contact: str, exclude_client_id: int
    ) -> bool:
        """True if any OTHER client already owns this contact number."""
        result = await self.db.execute(
            select(Client.client_id).where(
                Client.contact == contact,
                Client.client_id != exclude_client_id,
            )
        )
        return result.scalar_one_or_none() is not None

    # ── ClientTarget (macros) ────────────────────────────────────────

    async def get_client_target(self, client_id: int) -> Optional[ClientTarget]:
        result = await self.db.execute(
            select(ClientTarget).where(ClientTarget.client_id == client_id)
        )
        return result.scalar_one_or_none()

    async def upsert_client_target(
        self,
        client_id: int,
        calories: int,
        protein: int,
        carbs: int,
        fat: int,
        updated_at,
    ) -> None:
        target = await self.get_client_target(client_id)
        if target:
            target.calories = calories
            target.protein = protein
            target.carbs = carbs
            target.fat = fat
            target.updated_at = updated_at
        else:
            target = ClientTarget(
                client_id=client_id,
                calories=calories,
                protein=protein,
                carbs=carbs,
                fat=fat,
                updated_at=updated_at,
            )
            self.db.add(target)

    # ── Persistence helpers ──────────────────────────────────────────

    async def commit(self) -> None:
        await self.db.commit()

    async def rollback(self) -> None:
        await self.db.rollback()

    # ── Redis: pending contact-change state ──────────────────────────

    async def store_pending_contact_change(
        self,
        client_id: int,
        new_contact: str,
        old_otp: str,
        new_otp: str,
    ) -> None:
        """Atomically write the pending new-contact + both OTPs.

        All three keys share the same TTL so the change window expires together.
        Pipeline = one round-trip.
        """
        pipe = self.redis.pipeline(transaction=False)
        pipe.set(_pending_contact_key(client_id), new_contact, ex=PENDING_TTL)
        pipe.set(_old_otp_key(client_id), old_otp, ex=PENDING_TTL)
        pipe.set(_new_otp_key(client_id), new_otp, ex=PENDING_TTL)
        await pipe.execute()

    async def get_pending_contact(self, client_id: int) -> Optional[str]:
        raw = await self.redis.get(_pending_contact_key(client_id))
        if raw is None:
            return None
        return raw.decode() if isinstance(raw, bytes) else raw

    def old_otp_redis_key(self, client_id: int) -> str:
        return _old_otp_key(client_id)

    def new_otp_redis_key(self, client_id: int) -> str:
        return _new_otp_key(client_id)

    async def clear_pending_contact_change(self, client_id: int) -> None:
        pipe = self.redis.pipeline(transaction=False)
        pipe.delete(_pending_contact_key(client_id))
        pipe.delete(_old_otp_key(client_id))
        pipe.delete(_new_otp_key(client_id))
        await pipe.execute()
